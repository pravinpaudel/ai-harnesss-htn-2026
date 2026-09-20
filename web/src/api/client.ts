/** HTTP client for the harness API. */

import { readEvents } from "./sse";
import type {
  AnswerResponse, AppConfig, ConversationSummary, DatasetSummary, IngestReport, IngestRequest, RunSummary, Step,
} from "./types";

export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** The API admits four answers at a time and asks everyone else to come back. Not a failure. */
export class BusyError extends Error {
  readonly retryAfterSeconds: number;

  constructor(detail: string, retryAfterSeconds: number) {
    super(detail);
    this.name = "BusyError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

async function failure(response: Response): Promise<Error> {
  let detail = "";
  try {
    const payload = (await response.json()) as { detail?: string };
    detail = payload.detail ?? "";
  } catch {
    // A non-JSON error body still gets a useful message below.
  }
  if (response.status === 503) {
    const header = Number(response.headers?.get?.("Retry-After"));
    return new BusyError(detail || "The research service is busy answering other questions.",
      Number.isFinite(header) && header > 0 ? header : 5);
  }
  return new Error(detail || `The research service returned ${response.status}.`);
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { signal });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

function questionBody(question: string, sessionId: string) {
  return JSON.stringify({ question, session_id: sessionId });
}

function queryPath(route: string, dataset: string) {
  return `${apiBaseUrl}${route}?dataset=${encodeURIComponent(dataset)}`;
}

/** One answer, waited for in full. Used when the stream is unavailable. */
export async function askQuestion(question: string, sessionId: string, dataset = "latest",
                                  signal?: AbortSignal): Promise<AnswerResponse> {
  const response = await fetch(queryPath("/v1/queries", dataset), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: questionBody(question, sessionId),
    signal,
  });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as AnswerResponse;
}

export interface StreamHandlers {
  onRun?: (run: { run_id: string; dataset_version: string }) => void;
  onStep?: (step: Step) => void;
  /** Dataset name, or "latest" for the freshest ready version. */
  dataset?: string;
  signal?: AbortSignal;
}

/**
 * One answer, with each engine step as it happens. Falls back to the plain route when the stream
 * cannot be started or the body cannot be read; a 503 is re-thrown so the caller can say "busy".
 */
export async function streamAnswer(question: string, sessionId: string, handlers: StreamHandlers = {}): Promise<AnswerResponse> {
  const dataset = handlers.dataset ?? "latest";
  let response: Response;
  try {
    response = await fetch(queryPath("/v1/queries/stream", dataset), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: questionBody(question, sessionId),
      signal: handlers.signal,
    });
  } catch (error) {
    if (handlers.signal?.aborted) throw error;
    return askQuestion(question, sessionId, dataset, handlers.signal); // network or proxy trouble: wait it out
  }

  if (!response.ok) {
    const error = await failure(response);
    if (error instanceof BusyError) throw error;
    return askQuestion(question, sessionId, dataset, handlers.signal);
  }
  if (!response.body) return askQuestion(question, sessionId, dataset, handlers.signal);

  let answer: AnswerResponse | undefined;
  for await (const event of readEvents(response.body)) {
    if (event.type === "run") handlers.onRun?.(event.data);
    else if (event.type === "step") handlers.onStep?.(event.data);
    else if (event.type === "answer") answer = event.data;
    else if (event.type === "error") throw new Error(event.data.detail || "The engine could not finish this answer.");
  }
  if (!answer) throw new Error("The answer stream ended before an answer arrived.");
  return answer;
}

export function listDatasets(signal?: AbortSignal): Promise<DatasetSummary[]> {
  return getJson<DatasetSummary[]>("/v1/datasets", signal);
}

export function listRuns(sessionId: string, limit = 20, signal?: AbortSignal): Promise<RunSummary[]> {
  return getJson<RunSummary[]>(`/v1/runs?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`, signal);
}

export function listConversations(limit = 20, signal?: AbortSignal): Promise<ConversationSummary[]> {
  return getJson<ConversationSummary[]>(`/v1/conversations?limit=${limit}`, signal);
}

/** One stored run: the answer body it produced, when it finished with one. */
export async function getRunAnswer(runId: string, signal?: AbortSignal): Promise<AnswerResponse | undefined> {
  const payload = await getJson<{ run?: { response?: AnswerResponse | string } }>(`/v1/runs/${runId}`, signal);
  const stored = payload.run?.response;
  if (!stored) return undefined;
  if (typeof stored === "string") {
    try {
      return JSON.parse(stored) as AnswerResponse;
    } catch {
      return undefined;
    }
  }
  return stored;
}

export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${apiBaseUrl}/healthz`, { signal });
    return response.ok;
  } catch {
    return false;
  }
}

export function getConfig(signal?: AbortSignal): Promise<AppConfig> {
  return getJson<AppConfig>("/v1/config", signal);
}

/** Queue an ingest. The worker (`python -m app.worker.main`) is what actually runs it. */
export async function submitIngest(request: IngestRequest, signal?: AbortSignal): Promise<string> {
  const response = await fetch(`${apiBaseUrl}/v1/ingests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw await failure(response);
  const payload = (await response.json()) as { job_id: string };
  return payload.job_id;
}

/**
 * An ingest job's report, or `undefined` while it has not produced one.
 *
 * The API answers 409 for a job that is queued, running OR failed — there is no job-state route — so
 * a pending poll cannot tell "still working" from "gave up". The caller shows elapsed time instead.
 */
export async function getIngestReport(jobId: string, signal?: AbortSignal): Promise<IngestReport | undefined> {
  const response = await fetch(`${apiBaseUrl}/v1/ingests/${encodeURIComponent(jobId)}`, { signal });
  if (response.status === 409) return undefined;
  if (!response.ok) throw await failure(response);
  return (await response.json()) as IngestReport;
}
