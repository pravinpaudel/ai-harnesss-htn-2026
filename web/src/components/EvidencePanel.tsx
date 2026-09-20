import { useEffect, useRef } from "react";
import { ShieldCheck, X } from "lucide-react";
import type { Citation } from "../api/types";

/**
 * The selected source, in full. A side panel on a wide screen; a dialog over the conversation on a
 * narrow one (the CSS decides which), closable with Escape either way.
 */

interface Props {
  citation: Citation;
  onClose: () => void;
}

export default function EvidencePanel({ citation, onClose }: Props) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [citation, onClose]);

  const { span } = citation;
  return (
    <aside className="evidence is-open" aria-label="Source viewer">
      <div className="evidence-head">
        <h2 className="evidence-title" tabIndex={-1} ref={headingRef}>
          Source {citation.citation_id}
        </h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close source viewer">
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <dl className="evidence-meta">
        <dt>Document</dt>
        <dd className="mono">{span.document_name}</dd>
        <dt>Lines</dt>
        <dd className="mono">
          {span.line_start}–{span.line_end}
        </dd>
        {span.heading_path.length > 0 && (
          <>
            <dt>Section</dt>
            <dd>{span.heading_path.join(" › ")}</dd>
          </>
        )}
        <dt>Snapshot</dt>
        <dd className="mono">{span.document_hash.slice(0, 12)}</dd>
      </dl>
      <p className="evidence-verified">
        <ShieldCheck size={15} aria-hidden="true" /> Verified character-for-character against the snapshot
      </p>
      <blockquote className="quote quote-full">{span.exact_text}</blockquote>
    </aside>
  );
}
