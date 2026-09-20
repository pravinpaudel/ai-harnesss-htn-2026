import { Check, ChevronDown, Copy } from "lucide-react";
import { useState } from "react";
import type { AnswerResponse } from "../api/types";

interface Props {
  response: AnswerResponse;
}

function answerWithoutMarkers(answer: string): string {
  return answer.replace(/\s*\[\d+\]/g, "");
}

function answerWithCitations(response: AnswerResponse): string {
  const sources = response.citations.map((citation) => (
    `[${citation.citation_id}] ${citation.span.document_name}, lines ${citation.span.line_start}–${citation.span.line_end}\n${citation.span.exact_text}`
  ));
  return `${response.answer}${sources.length ? `\n\nSources\n${sources.join("\n\n")}` : ""}`;
}

export default function CopyMenu({ response }: Props) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"answer" | "citations" | undefined>();
  const [error, setError] = useState(false);

  async function copy(kind: "answer" | "citations") {
    try {
      await navigator.clipboard.writeText(kind === "answer" ? answerWithoutMarkers(response.answer) : answerWithCitations(response));
      setCopied(kind);
      setError(false);
      setOpen(false);
    } catch {
      setError(true);
    }
  }

  return (
    <div className="copy-menu">
      <button
        type="button"
        className="copy-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Copy answer options"
        onClick={() => setOpen((current) => !current)}
      >
        {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
        Copy <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <>
          <button type="button" className="menu-backdrop" aria-label="Close copy options" onClick={() => setOpen(false)} />
          <div className="copy-options" role="menu">
            <button type="button" role="menuitem" onClick={() => void copy("answer")}>Copy answer</button>
            <button type="button" role="menuitem" onClick={() => void copy("citations")}>Copy answer with citations</button>
          </div>
        </>
      )}
      {error && <span className="copy-error" role="alert">Copy unavailable</span>}
    </div>
  );
}
