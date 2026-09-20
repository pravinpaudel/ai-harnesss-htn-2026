import { AlertTriangle, Ban, Calculator, CheckCircle2, Scale } from "lucide-react";
import type { AnswerResponse, Step } from "../api/types";
import {
  declineCopy, evidenceCopy, formatSeconds, formatValue, periodLabel, ruleCopy, statusCopy,
} from "../format";
import AnswerProse from "./AnswerProse";
import CopyMenu from "./CopyMenu";
import SourceList from "./SourceList";
import StepList from "./StepList";

interface Props {
  response: AnswerResponse;
  steps?: Step[];
  activeCitationId?: number;
  onSelectCitation: (citationId: number) => void;
}

const statusIcon = {
  answered: CheckCircle2,
  partial: AlertTriangle,
  conflict: Scale,
  declined: Ban,
} as const;

function CitationChips({ ids, onSelect }: { ids: number[]; onSelect: (id: number) => void }) {
  return (
    <span className="chip-row">
      {ids.map((id) => (
        <button key={id} type="button" className="cite-marker" onClick={() => onSelect(id)} aria-label={`Show source ${id}`}>
          {id}
        </button>
      ))}
    </span>
  );
}

export default function AnswerCard({ response, steps, activeCitationId, onSelectCitation }: Props) {
  const copy = statusCopy[response.status];
  const StatusIcon = statusIcon[response.status];
  const citationIds = response.citations.map((citation) => citation.citation_id);
  const decline = response.decline_reason ? declineCopy[response.decline_reason] : undefined;

  return (
    <article className={`answer-card tone-${copy.tone}`} aria-label={`${copy.label} — research answer`}>
      <header className="answer-head">
        <span className={`status-badge tone-${copy.tone}`}>
          <StatusIcon size={15} aria-hidden="true" /> {copy.label}
        </span>
        <span className="evidence-status">{evidenceCopy[response.evidence_status]}</span>
        <span className="answer-head-spacer" />
        <CopyMenu response={response} />
        <span className="answer-run mono" title={`Run ${response.run_id}`}>
          {formatSeconds(response.provenance.usage.latency_ms)}
        </span>
      </header>
      <p className="status-blurb">{copy.blurb}</p>

      {decline && (
        <div className="notice notice-declined">
          <h3 className="notice-title">{decline.label}</h3>
          <p>{decline.blurb}</p>
        </div>
      )}

      <AnswerProse
        markdown={response.answer}
        citationIds={citationIds}
        activeId={activeCitationId}
        onSelect={onSelectCitation}
      />

      {response.values.length > 0 && (
        <section className="figures" aria-label="Key figures">
          <h3 className="section-heading">Key figures</h3>
          <ul className="figure-grid">
            {response.values.map((value, index) => (
              <li key={`${value.label}-${index}`} className="figure">
                <span className="figure-label">{value.label}</span>
                <span className="figure-value">{formatValue(value)}</span>
                <span className="figure-meta">
                  {periodLabel(value.period) && <span className="figure-period">{periodLabel(value.period)}</span>}
                  <CitationChips ids={value.citation_ids} onSelect={onSelectCitation} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {response.limitations.length > 0 && (
        <div className="notice notice-limits">
          <h3 className="notice-title">What this answer does not cover</h3>
          <ul>
            {response.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </div>
      )}

      {response.conflicts.map((conflict, index) => (
        <section className="conflict" key={conflict.finding_id ?? `${conflict.rule}-${index}`} aria-label="Conflicting evidence">
          <h3 className="section-heading">
            <Scale size={15} aria-hidden="true" /> {ruleCopy[conflict.rule] ?? conflict.rule}
          </h3>
          <p className="conflict-explanation">{conflict.explanation}</p>
          <ul className="claim-list">
            {conflict.claims.map((claim, claimIndex) => (
              <li key={`${claim.text}-${claimIndex}`} className="claim">
                <span className="claim-rank">Claim {claimIndex + 1}</span>
                <p>{claim.text}</p>
                <CitationChips ids={claim.citation_ids} onSelect={onSelectCitation} />
              </li>
            ))}
          </ul>
        </section>
      ))}

      {response.calculation_trace.length > 0 && (
        <details className="fold">
          <summary>
            <Calculator size={15} aria-hidden="true" /> How the numbers were worked out
          </summary>
          <ul className="calc-list">
            {response.calculation_trace.map((calculation, index) => (
              <li key={`${calculation.formula}-${index}`}>
                <code className="mono">{calculation.formula}</code>
                <span className="calc-result">
                  {calculation.rejected
                    ? `rejected — ${calculation.rejection_reason ?? "operands not comparable"}`
                    : `= ${calculation.result ?? "—"}`}
                </span>
                <span className="calc-operands">
                  {calculation.operands.map((operand) => operand.name).join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <SourceList citations={response.citations} activeId={activeCitationId} onSelect={onSelectCitation} />

      {steps && steps.length > 0 && (
        <details className="answer-trace">
          <summary>Research steps · {steps.length}</summary>
          <StepList steps={steps} />
        </details>
      )}
    </article>
  );
}
