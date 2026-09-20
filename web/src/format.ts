/** Display copy and number formatting. The wording carries the product's honesty, so it lives here. */

import type {
  AnswerStatus, AnswerValue, DeclineReason, EvidenceStatus, FindingRule, Period,
} from "./api/types";

export const statusCopy: Record<AnswerStatus, { label: string; blurb: string; tone: "ok" | "warn" | "conflict" | "declined" }> = {
  answered: { label: "Answered", blurb: "Every claim below is backed by verified source text.", tone: "ok" },
  partial: { label: "Partial answer", blurb: "Part of this question is supported by the corpus; the rest is not.", tone: "warn" },
  conflict: { label: "Sources disagree", blurb: "The corpus supports more than one answer. Both readings are shown.", tone: "conflict" },
  declined: { label: "Declined", blurb: "The corpus cannot support an answer, so none was guessed.", tone: "declined" },
};

export const evidenceCopy: Record<EvidenceStatus, string> = {
  fully_supported: "Fully supported",
  partial_support: "Partial support",
  conflicting: "Conflicting evidence",
  unsupported: "Unsupported",
};

export const declineCopy: Record<DeclineReason, { label: string; blurb: string }> = {
  insufficient_evidence: {
    label: "Not enough evidence",
    blurb: "Nothing in this corpus supports an answer to the question as asked.",
  },
  false_premise: {
    label: "The question assumes something the sources contradict",
    blurb: "Answering it would mean accepting a premise the corpus does not support.",
  },
  incompatible_currency: {
    label: "Currencies cannot be compared",
    blurb: "The figures involved are reported in different currencies, with no source-backed rate to convert them.",
  },
  ambiguous_period_or_basis: {
    label: "The period or basis is ambiguous",
    blurb: "More than one period or reporting basis fits the question, and the sources do not settle which.",
  },
  out_of_corpus_entity: {
    label: "That company is not in this corpus",
    blurb: "The question names something this dataset does not cover.",
  },
  future_data: {
    label: "That period is past the corpus cutoff",
    blurb: "The figures asked for had not been reported when this corpus was snapshotted.",
  },
  budget_exhausted: {
    label: "The query budget ran out",
    blurb: "The engine stopped before it could ground an answer. Asking something narrower usually works.",
  },
};

export const ruleCopy: Record<FindingRule, string> = {
  unit_currency_mix: "Mixed units or currencies",
  arithmetic: "Arithmetic inconsistency",
  rank_order: "Rank order inconsistency",
  count_claim: "Count claim inconsistency",
  trend_direction: "Trend direction inconsistency",
  duplicate_claim: "Duplicate claim, different values",
  period_order: "Period ordering problem",
};

export const stepKindCopy: Record<string, string> = {
  policy: "Policy",
  tool_call: "Evidence tool",
  llm: "Model",
  verify: "Verification",
};

const numberFormat = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 2 });

function num(value: number): string {
  return numberFormat.format(value);
}

/** One asserted figure, formatted by its unit. Ranges keep both ends; text values pass through. */
export function formatValue(value: AnswerValue): string {
  if (value.value_text) return value.value_text;
  const parts: string[] = [];
  if (value.value !== undefined) parts.push(num(value.value));
  else if (value.value_low !== undefined || value.value_high !== undefined) {
    parts.push(`${value.value_low !== undefined ? num(value.value_low) : "?"}–${value.value_high !== undefined ? num(value.value_high) : "?"}`);
  }
  if (parts.length === 0) return "—";
  const body = parts[0];
  switch (value.unit) {
    case "money":
      return value.currency ? `${value.currency} ${body}` : body;
    case "pct":
      return `${body}%`;
    case "bps":
      return `${body} bps`;
    case "x":
      return `${body}×`;
    case "rank":
      return `#${body}`;
    default:
      return body;
  }
}

export function periodLabel(period?: Period): string | undefined {
  if (!period) return undefined;
  return period.label ?? period.end ?? undefined;
}

/** "just now", "12 min ago", "3 days ago" — for dataset freshness. */
export function relativeTime(iso: string | undefined, now = Date.now()): string | undefined {
  if (!iso) return undefined;
  const then = Date.parse(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return undefined;
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 60) return "just now";
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "minute"], [3600, "hour"], [86400, "day"], [604800, "week"], [2629800, "month"], [31557600, "year"],
  ];
  let chosen: [number, Intl.RelativeTimeFormatUnit] = units[0];
  for (const unit of units) if (seconds >= unit[0]) chosen = unit;
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  return format.format(-Math.round(seconds / chosen[0]), chosen[1]);
}

export function formatCost(usd: number): string {
  if (!usd) return "$0.00";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

export function shortHash(hash: string): string {
  return hash.slice(0, 12);
}
