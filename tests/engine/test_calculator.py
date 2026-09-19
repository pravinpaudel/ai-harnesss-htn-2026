from contracts.models import Basis, CalculationRequest, Operand, Operation, Period, PeriodType, SourceSpan, Unit
from app.tools.calculator import calculate

SPAN = SourceSpan(document_name="d.md", document_hash="0" * 64, line_start=1, line_end=1, char_start=0,
                  char_end=1, exact_text="x")


def op(name, value, unit=Unit.money, currency=None, basis=None):
    return Operand(name=name, value=value, unit=unit, currency=currency, basis=basis,
                   period=Period(label="Q", type=PeriodType.calendar), span=SPAN)


def test_percent_change_matches_fixture_fx04():
    r = calculate(CalculationRequest(operation=Operation.percent_change,
                                     operands=[op("Q1", 165.5e6), op("Q2", 152.6e6)], rounding=2))
    assert not r.rejected and r.result == -7.79 and r.unit == Unit.pct
    assert r.formula == "(152.6M - 165.5M) / 165.5M x 100"


def test_rank_orders_descending_and_lists_items():
    r = calculate(CalculationRequest(operation=Operation.rank, operands=[
        op("AEM", 35, Unit.pct), op("IVN", 58, Unit.pct), op("ABX", 44, Unit.pct)]))
    assert r.result_items == ["IVN", "ABX", "AEM"]


def test_rejects_mixed_currency_without_conversion():
    r = calculate(CalculationRequest(operation=Operation.compare, operands=[
        op("FM", 30e9, currency="CAD"), op("TECK.B", 32e9, currency="USD")]))
    assert r.rejected and "currencies" in r.rejection_reason and r.result is None


def test_rejects_mixed_units_and_bases():
    assert calculate(CalculationRequest(operation=Operation.sum, operands=[
        op("a", 1, Unit.pct), op("b", 2, Unit.money)])).rejected
    r = calculate(CalculationRequest(operation=Operation.difference, operands=[
        op("adj", 0.48, basis=Basis.adjusted), op("gaap", 0.57, basis=Basis.gaap)]))
    assert r.rejected and "bases" in r.rejection_reason


def test_division_by_zero_is_rejected_not_raised():
    r = calculate(CalculationRequest(operation=Operation.percent_change, operands=[op("a", 0), op("b", 1)]))
    assert r.rejected


def test_average_and_median():
    ops = [op("a", 1.0), op("b", 2.0), op("c", 6.0)]
    assert calculate(CalculationRequest(operation=Operation.average, operands=ops)).result == 3.0
    assert calculate(CalculationRequest(operation=Operation.median, operands=ops)).result == 2.0

