import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Clock, ListChecks, MessageSquarePlus, RefreshCw } from "lucide-react";
import {
  BusyError, checkHealth, getRunAnswer, listDatasets, listRuns, streamAnswer,
} from "./api/client";
import type { AnswerResponse, Citation, DatasetSummary, Step } from "./api/types";
import { loadSessionId, resetSessionId, storeSessionId } from "./session";
import AnswerCard from "./components/AnswerCard";
import Composer from "./components/Composer";
import ConversationHistory from "./components/ConversationHistory";
import ConnectView from "./components/ConnectView";
import DatasetBar from "./components/DatasetBar";
import EvidencePanel from "./components/EvidencePanel";
import QuestionLibrary from "./components/QuestionLibrary";
import StepList from "./components/StepList";

type Entry =
  | { kind: "question"; id: string; text: string }
  | { kind: "answer"; id: string; response: AnswerResponse; steps?: Step[] }
  | { kind: "problem"; id: string; variant: "busy" | "error"; detail: string; retryAfter?: number; question: string };

type View = "workspace" | "connect";

function id() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `e-${Math.random().toString(36).slice(2)}`;
}

export default function App() {
  const [sessionId, setSessionId] = useState(loadSessionId);
  const [view, setView] = useState<View>("workspace");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [running, setRunning] = useState(false);
  const [liveSteps, setLiveSteps] = useState<Step[]>([]);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [chosenDataset, setChosenDataset] = useState<string>();
  const [online, setOnline] = useState<boolean | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [active, setActive] = useState<{ runId: string; citationId: number } | undefined>();
  const [questionsOpen, setQuestionsOpen] = useState(false);
  const conversationRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const answerRefs = useRef(new Map<string, HTMLDivElement>());

  const datasetName = chosenDataset ?? datasets[0]?.name;
  const dataset = datasets.find((candidate) => candidate.name === datasetName) ?? datasets[0];

  const refreshDatasets = useCallback(() => {
    void listDatasets()
      .then(setDatasets)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    void checkHealth(abort.signal).then(setOnline);
    return () => abort.abort();
  }, []);

  useEffect(refreshDatasets, [refreshDatasets]);

  // History survives a reload: the session id is stable, and every run is stored with its answer.
  useEffect(() => {
    const abort = new AbortController();
    setRestoring(true);
    (async () => {
      try {
        const runs = await listRuns(sessionId, 20, abort.signal);
        const finished = runs.filter((run) => run.status).reverse();
        const restored: Entry[] = [];
        for (const run of finished) {
          restored.push({ kind: "question", id: `q-${run.run_id}`, text: run.question });
          const response = await getRunAnswer(run.run_id, abort.signal);
          if (response) restored.push({ kind: "answer", id: `a-${run.run_id}`, response });
        }
        if (restored.length > 0) setEntries((current) => (current.length > 0 ? current : restored));
      } catch {
        // No history is a fine outcome: a fresh session, or an API that is not up yet.
      } finally {
        if (!abort.signal.aborted) setRestoring(false);
      }
    })();
    return () => abort.abort();
  }, [sessionId]);

  useEffect(() => {
    const conversation = conversationRef.current;
    if (!running || entries[entries.length - 1]?.kind === "answer") return;
    if (typeof conversation?.scrollTo === "function") {
      conversation.scrollTo({ top: conversation.scrollHeight, behavior: "smooth" });
    }
  }, [entries, liveSteps.length, running]);

  useEffect(() => {
    const recent = entries[entries.length - 1];
    if (recent?.kind !== "answer") return;
    answerRefs.current.get(recent.id)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [entries]);

  const ask = useCallback(
    async (question: string) => {
      setEntries((current) => [...current, { kind: "question", id: id(), text: question }]);
      setRunning(true);
      setLiveSteps([]);
      const steps: Step[] = [];
      try {
        const response = await streamAnswer(question, sessionId, {
          dataset: datasetName ?? "latest",
          onStep: (step) => {
            steps.push(step);
            setLiveSteps([...steps]);
          },
        });
        // keyed by run id, the same key history restores it under, so a restore re-renders the card
        // rather than remounting it and dropping what the reader had open
        setEntries((current) => [...current, { kind: "answer", id: `a-${response.run_id}`, response, steps: [...steps] }]);
      } catch (error) {
        const problem: Entry =
          error instanceof BusyError
            ? { kind: "problem", id: id(), variant: "busy", detail: error.message, retryAfter: error.retryAfterSeconds, question }
            : {
                kind: "problem",
                id: id(),
                variant: "error",
                detail: error instanceof Error ? error.message : "The research service could not be reached.",
                question,
              };
        setEntries((current) => [...current, problem]);
      } finally {
        setRunning(false);
        setLiveSteps([]);
      }
    },
    [sessionId, datasetName],
  );

  function submit() {
    const question = draft.trim();
    if (!question || running) return;
    setDraft("");
    void ask(question);
  }

  function pickQuestion(question: string) {
    setView("workspace");
    setDraft(question);
    composerRef.current?.focus();
  }

  function retry(entry: Extract<Entry, { kind: "problem" }>) {
    setEntries((current) => current.filter((item) => item.id !== entry.id));
    void ask(entry.question);
  }

  function startNewConversation() {
    setSessionId(resetSessionId());
    setEntries([]);
    setDraft("");
    setActive(undefined);
    setView("workspace");
  }

  function openConversation(nextSessionId: string) {
    if (nextSessionId === sessionId) return;
    setEntries([]);
    setDraft("");
    setActive(undefined);
    setSessionId(storeSessionId(nextSessionId));
  }

  function useDataset(name: string) {
    setChosenDataset(name);
    setView("workspace");
  }

  const activeCitation: Citation | undefined = useMemo(() => {
    if (!active) return undefined;
    for (const entry of entries) {
      if (entry.kind === "answer" && entry.response.run_id === active.runId) {
        return entry.response.citations.find((citation) => citation.citation_id === active.citationId);
      }
    }
    return undefined;
  }, [active, entries]);

  const askedQuestions = useMemo(
    () => entries.filter((entry): entry is Extract<Entry, { kind: "question" }> => entry.kind === "question").map((entry) => entry.text),
    [entries],
  );

  const lastStep = liveSteps[liveSteps.length - 1];

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <img className="brand-logo" src="/rbc-logo.png" alt="RBC" />
          <div>
            <h1>FinRet Research Desk</h1>
            <p>Cited answers over ingested research</p>
          </div>
        </div>
        <nav className="views" aria-label="Views">
          <button
            type="button"
            className={`view-tab${view === "workspace" ? " is-active" : ""}`}
            aria-current={view === "workspace" ? "page" : undefined}
            onClick={() => setView("workspace")}
          >
            Research
          </button>
          <button
            type="button"
            className={`view-tab${view === "connect" ? " is-active" : ""}`}
            aria-current={view === "connect" ? "page" : undefined}
            onClick={() => setView("connect")}
          >
            Sources
          </button>
        </nav>
        <ConversationHistory activeSessionId={sessionId} onSelect={openConversation} />
        <button type="button" className="ghost-button" onClick={startNewConversation}>
          <MessageSquarePlus size={16} aria-hidden="true" /> New conversation
        </button>
      </header>
      <DatasetBar dataset={dataset} online={online} />

      {view === "connect" ? (
        <main className="connect-layout">
          <ConnectView
            datasets={datasets}
            activeDataset={datasetName}
            onUseDataset={useDataset}
            onDatasetsChanged={refreshDatasets}
          />
        </main>
      ) : (
        <main className="workspace">
          {!questionsOpen && (
            <button type="button" className="question-sidebar-toggle" onClick={() => setQuestionsOpen(true)}>
              <ListChecks size={16} aria-hidden="true" /> Questions
            </button>
          )}
          <div className={`panes${questionsOpen ? " questions-open" : ""}${activeCitation ? " has-evidence" : ""}`}>
            {questionsOpen && (
              <div className="library-pane">
                <QuestionLibrary
                  datasetName={datasetName}
                  askedQuestions={askedQuestions}
                  onPick={pickQuestion}
                  onClose={() => setQuestionsOpen(false)}
                />
              </div>
            )}

            <section className="conversation" ref={conversationRef}>
              <div className="conversation-inner">
                {restoring && entries.length === 0 && (
                  <p className="restoring" role="status">
                    <Clock size={14} aria-hidden="true" /> Restoring this conversation…
                  </p>
                )}

                {entries.length === 0 && !restoring && (
                  <div className="empty-state">
                    <h2>Ask a question about the corpus</h2>
                    <p>
                      Every claim comes back with a <span className="cite-marker is-static">n</span> marker you can open
                      to read the exact source line. Questions the corpus cannot support are declined rather than
                      guessed.
                    </p>
                    <p className="empty-hint">Pick one from the library, or write your own below.</p>
                  </div>
                )}

                {entries.map((entry) => {
                  if (entry.kind === "question") {
                    return (
                      <p className="question-bubble" key={entry.id}>
                        {entry.text}
                      </p>
                    );
                  }
                  if (entry.kind === "answer") {
                    return (
                      <div
                        key={entry.id}
                        ref={(element) => {
                          if (element) answerRefs.current.set(entry.id, element);
                          else answerRefs.current.delete(entry.id);
                        }}
                      >
                        <AnswerCard
                          response={entry.response}
                          steps={entry.steps}
                          activeCitationId={active?.runId === entry.response.run_id ? active.citationId : undefined}
                          onSelectCitation={(citationId) => setActive({ runId: entry.response.run_id, citationId })}
                        />
                      </div>
                    );
                  }
                  return (
                    <div className={`notice notice-${entry.variant}`} key={entry.id} role="alert">
                      <h3 className="notice-title">
                        {entry.variant === "busy" ? "The desk is at capacity" : "That question did not complete"}
                      </h3>
                      <p>
                        {entry.variant === "busy"
                          ? `Four answers are already being worked on. Try again in about ${entry.retryAfter ?? 5} seconds — nothing was lost.`
                          : entry.detail}
                      </p>
                      <button type="button" className="ghost-button" onClick={() => retry(entry)} disabled={running}>
                        <RefreshCw size={15} aria-hidden="true" /> Ask it again
                      </button>
                    </div>
                  );
                })}

                {running && (
                  <div className="working" aria-label="Building the answer">
                    <p className="working-head">
                      <span className="dots" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                      Searching the corpus and verifying citations — usually 10 to 20 seconds
                    </p>
                    <StepList steps={liveSteps} live />
                  </div>
                )}
                <p className="sr-only" role="status" aria-live="polite">
                  {running
                    ? lastStep
                      ? `Working: step ${lastStep.seq}, ${lastStep.name}`
                      : "Working on the answer"
                    : entries.length > 0 && entries[entries.length - 1].kind === "answer"
                      ? "Answer ready"
                      : ""}
                </p>
              </div>

              <div className="composer-shell">
                <div className="conversation-inner">
                  <Composer value={draft} onChange={setDraft} onSubmit={submit} busy={running} inputRef={composerRef} />
                  <p className="composer-note">
                    Answers are grounded in {datasetName ?? "the loaded dataset"} and cite document and line numbers.
                    Enter sends; Shift+Enter adds a line.
                  </p>
                </div>
              </div>
            </section>

            {activeCitation && <EvidencePanel citation={activeCitation} onClose={() => setActive(undefined)} />}
          </div>
        </main>
      )}
    </div>
  );
}
