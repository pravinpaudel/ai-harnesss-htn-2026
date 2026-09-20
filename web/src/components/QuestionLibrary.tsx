import { useState } from "react";
import { Check, ListChecks, PanelLeftClose } from "lucide-react";
import library from "../data/questions.json";

/**
 * The benchmark questions, offered whatever corpus is loaded.
 *
 * The question set belongs to the benchmark, not to a dataset version: judging day serves a corpus
 * of the same shape under whatever name it is ingested as. A set keyed to the loaded dataset wins
 * when one exists; otherwise the first set is shown, and a line says which corpus it was written
 * against so an answer key is never read as applying to a corpus it does not describe.
 */

interface Props {
  datasetName?: string;
  askedQuestions: string[];
  onPick: (question: string) => void;
  onClose: () => void;
}

interface LibraryQuestion {
  n: number;
  question: string;
  expected: string;
  period: string;
  confidence: string;
}

export default function QuestionLibrary({ datasetName, askedQuestions, onPick, onClose }: Props) {
  const set = library.sets.find((candidate) => candidate.dataset === datasetName) ?? library.sets[0];
  const writtenForThisCorpus = !datasetName || set?.dataset === datasetName;
  const [groupId, setGroupId] = useState(set?.groups[0]?.id ?? "");
  const [showExpected, setShowExpected] = useState(false);
  const asked = new Set(askedQuestions);

  if (!set) {
    return (
      <section className="library" aria-label="Question library">
        <header className="library-head">
          <h2 className="panel-title">
            <ListChecks size={16} aria-hidden="true" /> Questions
          </h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Hide questions">
            <PanelLeftClose size={17} aria-hidden="true" />
          </button>
        </header>
        <p className="library-note">
          No benchmark set for {datasetName ? <span className="mono">{datasetName}</span> : "this dataset"}. Ask
          anything about the corpus — these are a starting point.
        </p>
        <ul className="question-list">
          {library.generic.map((question) => (
            <li key={question}>
              <button type="button" className="question" onClick={() => onPick(question)}>
                <span className="question-text">{question}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  const group = set.groups.find((candidate) => candidate.id === groupId) ?? set.groups[0];
  const total = set.groups.reduce((count, candidate) => count + candidate.questions.length, 0);

  return (
    <section className="library" aria-label="Question library">
      <header className="library-head">
        <h2 className="panel-title">
          <ListChecks size={16} aria-hidden="true" /> {set.title}
        </h2>
        <span className="library-count">{total} questions</span>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Hide questions">
          <PanelLeftClose size={17} aria-hidden="true" />
        </button>
      </header>
      <div role="tablist" aria-label="Sector" className="library-tabs">
        {set.groups.map((candidate) => (
          <button
            key={candidate.id}
            role="tab"
            type="button"
            aria-selected={candidate.id === group.id}
            className={`library-tab${candidate.id === group.id ? " is-active" : ""}`}
            onClick={() => setGroupId(candidate.id)}
          >
            {candidate.label}
          </button>
        ))}
      </div>
      <ul className="question-list">
        {(group.questions as LibraryQuestion[]).map((item) => (
          <li key={`${group.id}-${item.n}`}>
            <button
              type="button"
              className={`question${asked.has(item.question) ? " is-asked" : ""}`}
              onClick={() => onPick(item.question)}
            >
              <span className="question-number mono" aria-hidden="true">
                {String(item.n).padStart(2, "0")}
              </span>
              <span className="question-body">
                <span className="question-text">{item.question}</span>
                {showExpected && (
                  <span className="question-expected mono">
                    expected {item.expected} · {item.period} · {item.confidence.toLowerCase()} confidence
                  </span>
                )}
              </span>
              {asked.has(item.question) && (
                <span className="question-asked" aria-label="Asked in this conversation">
                  <Check size={14} aria-hidden="true" />
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
      <label className="library-toggle">
        <input type="checkbox" checked={showExpected} onChange={(event) => setShowExpected(event.target.checked)} />
        Show the benchmark answer key
      </label>
      <p className="library-note">
        {set.note}
        {!writtenForThisCorpus && (
          <>
            {" "}
            Written against <span className="mono">{set.dataset}</span>; the loaded corpus is{" "}
            <span className="mono">{datasetName}</span>, so the answer key may not line up.
          </>
        )}
      </p>
    </section>
  );
}
