import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { FileText, MessageSquarePlus, Send, ShieldCheck } from "lucide-react";
import { AnswerResponse, askQuestion, checkHealth } from "./api";

type Message =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "assistant"; response: AnswerResponse };

const statusLabels: Record<AnswerResponse["status"], string> = {
  answered: "Answered",
  partial: "Partially supported",
  conflict: "Conflicting evidence",
  declined: "Unable to answer",
};

function newSessionId() {
  return crypto.randomUUID();
}

function AnswerCard({ response }: { response: AnswerResponse }) {
  return (
    <article className="answer-card" aria-label="Research answer">
      {response.status !== "answered" && (
        <div className={`status status-${response.status}`}>
          <ShieldCheck size={15} aria-hidden="true" /> {statusLabels[response.status]}
        </div>
      )}
      <div className="answer-copy"><ReactMarkdown>{response.answer}</ReactMarkdown></div>

      {response.limitations.length > 0 && (
        <p className="notice"><strong>Limitations:</strong> {response.limitations.join(" ")}</p>
      )}
      {response.decline_reason && (
        <p className="notice"><strong>Reason:</strong> {response.decline_reason.replaceAll("_", " ")}</p>
      )}

      {response.citations.length > 0 && (
        <details className="citations" aria-label="Sources">
          <summary><FileText size={16} aria-hidden="true" /> Sources ({response.citations.length})</summary>
          <ol>
            {response.citations.map((citation) => (
              <li key={citation.citation_id}>
                <span className="citation-label">[{citation.citation_id}] {citation.span.document_name}, lines {citation.span.line_start}–{citation.span.line_end}</span>
                <blockquote>{citation.span.exact_text}</blockquote>
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState(newSessionId);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serviceOnline, setServiceOnline] = useState<boolean | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    void checkHealth().then(setServiceOnline);
  }, []);

  function startNewConversation() {
    setMessages([]);
    setQuestion("");
    setError(null);
    setSessionId(newSessionId());
    if (inputRef.current) inputRef.current.style.height = "auto";
    inputRef.current?.focus();
  }

  function resizeComposer(textarea: HTMLTextAreaElement) {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }

  function changeQuestion(value: string) {
    setQuestion(value);
    if (inputRef.current) resizeComposer(inputRef.current);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || isLoading) return;

    setError(null);
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: trimmedQuestion }]);
    setQuestion("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setIsLoading(true);
    try {
      const response = await askQuestion(trimmedQuestion, sessionId);
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: "assistant", response }]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to complete this question.");
    } finally {
      setIsLoading(false);
      inputRef.current?.focus();
    }
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">FR</span><div><h1>Finance Research Harness</h1><p>Evidence-backed research workspace</p></div></div>
        <div className="header-actions"><span className={`service-status ${serviceOnline === false ? "offline" : ""}`}><i /> {serviceOnline === null ? "Checking API" : serviceOnline ? "Research API online" : "Research API offline"}</span><button className="new-chat" onClick={startNewConversation}><MessageSquarePlus size={17} /> New conversation</button></div>
      </header>

      <section className="conversation" aria-live="polite">
        {messages.length === 0 ? (
          <div className="empty-state"><span className="empty-icon"><ShieldCheck size={28} /></span><h2>Ask a research question</h2><p>Answers are grounded in the latest available dataset and include verified source citations.</p><div className="suggestions"><button onClick={() => setQuestion("Which company has the highest revenue growth?")}>Compare company performance</button><button onClick={() => setQuestion("What conflicts exist in the reported results?")}>Review conflicting evidence</button></div></div>
        ) : messages.map((message) => message.role === "user" ? <div className="message user-message" key={message.id}>{message.content}</div> : <AnswerCard key={message.id} response={message.response} />)}
        {isLoading && <div className="loading" role="status"><span /><span /><span /> Researching the evidence…</div>}
      </section>

      <footer className="composer-shell">
        {error && <p className="error" role="alert">{error}</p>}
        <form className="composer" onSubmit={submit}>
          <label className="sr-only" htmlFor="question">Research question</label>
          <textarea ref={inputRef} id="question" value={question} onChange={(event) => changeQuestion(event.target.value)} placeholder="Ask a question about the latest research dataset…" rows={2} disabled={isLoading} />
          <button type="submit" disabled={isLoading || !question.trim()} aria-label="Send question"><Send size={19} /></button>
        </form>
        <p className="composer-note">Responses cite source documents and line ranges. Latest dataset is selected automatically.</p>
      </footer>
    </main>
  );
}
