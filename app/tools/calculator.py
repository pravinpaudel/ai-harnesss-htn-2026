"""Deterministic calculator. Every result carries its formula and cited operands.

Guards never raise: an incompatible request returns rejected=True with a reason, so the
answer policy can decline honestly instead of computing a meaningless number.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from statistics import median

from contracts.models import CalculationRequest, CalculationResult, Operand, Operation, Unit

_SCALES = [(Decimal(10) ** 9, "B"), (Decimal(10) ** 6, "M"), (Decimal(10) ** 3, "K")]


def _fmt(v: float, unit: Unit) -> str:
    d = Decimal(str(v))
    if unit == Unit.money:
        for size, suffix in _SCALES:
            if abs(d) >= size:
                return f"{(d / size).normalize():f}{suffix}"
    return f"{d.normalize():f}"


def _round(x: Decimal, places: int) -> float:
    return float(x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def _reject(req: CalculationRequest, unit: Unit, reason: str) -> CalculationResult:
    return CalculationResult(operation=req.operation, formula="", operands=req.operands, result=None,
                             unit=unit, rounding=req.rounding, rejected=True, rejection_reason=reason)


def _guard(req: CalculationRequest) -> str | None:
    ops = req.operands
    units = {o.unit for o in ops}
    if len(units) > 1:
        return "operands have different units: " + ", ".join(sorted(u.value for u in units))
    currencies = {o.currency for o in ops if o.currency}
    if len(currencies) > 1 and not req.allow_mixed_currency:
        return ("operands are in different currencies (" + ", ".join(sorted(currencies))
                + ") and no source-backed conversion was supplied")
    bases = {o.basis for o in ops}
    if len(bases) > 1:
        return "operands mix reporting bases: " + ", ".join(sorted(b.value if b else "unstated" for b in bases))
    return None


def calculate(req: CalculationRequest) -> CalculationResult:
    ops = req.operands
    unit = ops[0].unit
    reason = _guard(req)
    if reason:
        return _reject(req, unit, reason)
    currency = next((o.currency for o in ops if o.currency), None)
    vals = [Decimal(str(o.value)) for o in ops]
    names = [_fmt(o.value, o.unit) for o in ops]
    op = req.operation
    try:
        if op in (Operation.difference, Operation.percent_change, Operation.ratio):
            if len(ops) != 2:
                return _reject(req, unit, f"{op.value} needs exactly 2 operands (first = from/numerator)")
            a, b = vals
            if op == Operation.difference:
                return _ok(req, f"{names[1]} - {names[0]}", b - a, unit, currency)
            if a == 0:
                return _reject(req, unit, "division by zero (first operand is 0)")
            if op == Operation.percent_change:
                return _ok(req, f"({names[1]} - {names[0]}) / {names[0]} x 100", (b - a) / abs(a) * 100,
                           Unit.pct, None)
            return _ok(req, f"{names[1]} / {names[0]}", b / a, Unit.ratio, None)
        if op == Operation.sum:
            return _ok(req, " + ".join(names), sum(vals, Decimal(0)), unit, currency)
        if op == Operation.average:
            return _ok(req, f"({' + '.join(names)}) / {len(vals)}", sum(vals, Decimal(0)) / len(vals), unit, currency)
        if op == Operation.median:
            return _ok(req, f"median({', '.join(names)})", Decimal(str(median(vals))), unit, currency)
        if op in (Operation.rank, Operation.compare):
            order = sorted(range(len(ops)), key=lambda i: vals[i], reverse=True)
            items = [ops[i].name for i in order]
            formula = " > ".join(f"{ops[i].name} ({names[i]})" for i in order)
            return CalculationResult(operation=op, formula=formula, operands=ops, result=float(vals[order[0]]),
                                     result_items=items, unit=unit, currency=currency, rounding=req.rounding)
    except (InvalidOperation, ZeroDivisionError) as e:
        return _reject(req, unit, f"arithmetic error: {e}")
    return _reject(req, unit, f"unsupported operation {op.value}")


def _ok(req: CalculationRequest, formula: str, x: Decimal, unit: Unit, currency: str | None) -> CalculationResult:
    return CalculationResult(operation=req.operation, formula=formula, operands=req.operands,
                             result=_round(x, req.rounding), unit=unit, currency=currency, rounding=req.rounding)


def operand_label(o: Operand) -> str:
    return f"{o.name} = {_fmt(o.value, o.unit)}{' ' + o.currency if o.currency else ''}"
