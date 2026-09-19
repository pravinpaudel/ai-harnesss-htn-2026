export type AnswerStatus = "answered" | "conflict" | "declined" | "partial";

export interface Citation {
  citation_id: number;
  evidence_kind: "chunk" | "table_cell" | "fact";
  span: {
    document_name: string;
    line_start: number;
    line_end: number;
    exact_text: string;
  };
}

export interface AnswerResponse {
  run_id: string;
  status: AnswerStatus;
  answer: string;
  citations: Citation[];
  limitations: string[];
  decline_reason: string | null;
  evidence_status: string;
  dataset_version: string;
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export async function askQuestion(question: string, sessionId: string): Promise<AnswerResponse> {
  const response = await fetch(`${apiBaseUrl}/v1/queries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session_id: sessionId }),
  });

  if (!response.ok) {
    let detail = "Unable to reach the research service.";
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail || detail;
    } catch {
      // A non-JSON error response still receives the useful fallback message.
    }
    throw new Error(detail);
  }

  return response.json() as Promise<AnswerResponse>;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${apiBaseUrl}/healthz`);
    return response.ok;
  } catch {
    return false;
  }
}
