import { FileText } from "lucide-react";
import type { Citation } from "../api/types";

/**
 * The evidence behind one answer, in the open — not folded away behind a disclosure.
 *
 * `span.exact_text` is verified source text: it is rendered verbatim. Long quotes are visually
 * clamped with CSS (which changes no characters) and the full text is one click away.
 */

interface Props {
  citations: Citation[];
  activeId?: number;
  onSelect: (citationId: number) => void;
}

const kindCopy: Record<Citation["evidence_kind"], string> = {
  chunk: "narrative",
  table_cell: "table cell",
  fact: "typed fact",
};

export default function SourceList({ citations, activeId, onSelect }: Props) {
  if (citations.length === 0) return null;
  return (
    <section className="sources" aria-label={`Sources (${citations.length})`}>
      <h3 className="section-heading">
        <FileText size={15} aria-hidden="true" /> Sources ({citations.length})
      </h3>
      <ol className="source-list">
        {citations.map((citation) => {
          const { span } = citation;
          return (
            <li key={citation.citation_id} className={`source-item${activeId === citation.citation_id ? " is-active" : ""}`}>
              <button
                type="button"
                className="source-open"
                onClick={() => onSelect(citation.citation_id)}
                aria-label={`Show source ${citation.citation_id} in full`}
              >
                <span className="cite-badge">{citation.citation_id}</span>
                <span className="source-where">
                  <span className="source-doc">{span.document_name}</span>
                  <span className="source-lines">
                    lines {span.line_start}–{span.line_end} · {kindCopy[citation.evidence_kind]}
                  </span>
                </span>
              </button>
              {span.heading_path.length > 0 && (
                <p className="source-path">{span.heading_path.join(" › ")}</p>
              )}
              <blockquote className="quote quote-clamped">{span.exact_text}</blockquote>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
