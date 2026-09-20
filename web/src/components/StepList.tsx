import type { Step } from "../api/types";
import { stepKindCopy } from "../format";

/** The engine's steps: live while an answer is being built, and kept with the answer afterwards. */

export default function StepList({ steps, live = false }: { steps: Step[]; live?: boolean }) {
  return (
    <ol className="step-list">
      {steps.map((step) => (
        <li key={step.seq} className={`step step-${step.kind}`}>
          <span className="step-kind">{stepKindCopy[step.kind] ?? step.kind}</span>
          <span className="step-name mono">{step.name}</span>
          {step.detail && <span className="step-detail">{step.detail}</span>}
          {step.latency_ms ? <span className="step-latency">{step.latency_ms} ms</span> : null}
        </li>
      ))}
      {live && (
        <li className="step step-pending" aria-hidden="true">
          <span className="step-kind">Working</span>
          <span className="dots">
            <i />
            <i />
            <i />
          </span>
        </li>
      )}
    </ol>
  );
}
