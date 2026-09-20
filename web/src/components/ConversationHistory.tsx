import { ChevronDown, History, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { listConversations } from "../api/client";
import type { ConversationSummary } from "../api/types";
import { relativeTime } from "../format";

interface Props {
  activeSessionId: string;
  onSelect: (sessionId: string) => void;
}

export default function ConversationHistory({ activeSessionId, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    const abort = new AbortController();
    setLoading(true);
    void listConversations(20, abort.signal)
      .then(setConversations)
      .catch(() => setConversations([]))
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [open]);

  return (
    <div className="history-menu">
      <button
        type="button"
        className="ghost-button history-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <History size={16} aria-hidden="true" /> History <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <>
          <button type="button" className="menu-backdrop" aria-label="Close conversation history" onClick={() => setOpen(false)} />
          <div className="history-options" role="menu" aria-label="Recent conversations">
            {loading && <p className="history-state"><LoaderCircle size={15} aria-hidden="true" /> Loading conversations…</p>}
            {!loading && conversations.length === 0 && <p className="history-state">No saved conversations yet.</p>}
            {!loading && conversations.map((conversation) => (
              <button
                type="button"
                key={conversation.session_id}
                role="menuitem"
                className={`history-option${conversation.session_id === activeSessionId ? " is-active" : ""}`}
                onClick={() => {
                  setOpen(false);
                  onSelect(conversation.session_id);
                }}
              >
                <span>{conversation.title}</span>
                <small>{conversation.run_count} {conversation.run_count === 1 ? "answer" : "answers"} · {relativeTime(conversation.updated_at)}</small>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
