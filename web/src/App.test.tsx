import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  askQuestion: vi.fn(),
  checkHealth: vi.fn(),
}));

const answeredResponse: api.AnswerResponse = {
  run_id: "run-1", status: "answered", answer: "Revenue was **$152.6M** [1].", limitations: [], decline_reason: null,
  evidence_status: "fully_supported", dataset_version: "version-1",
  citations: [{ citation_id: 1, evidence_kind: "fact", span: { document_name: "mining.md", line_start: 95, line_end: 95, exact_text: "Revenue $152.6M" } }],
};

beforeEach(() => {
  vi.mocked(api.askQuestion).mockReset();
  vi.mocked(api.checkHealth).mockResolvedValue(true);
});

it("submits a question and renders the cited answer", async () => {
  vi.mocked(api.askQuestion).mockResolvedValue(answeredResponse);
  render(<App />);
  fireEvent.change(screen.getByLabelText("Research question"), { target: { value: "What was revenue?" } });
  fireEvent.click(screen.getByLabelText("Send question"));

  expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
  await waitFor(() => expect(api.askQuestion).toHaveBeenCalledWith("What was revenue?", expect.any(String)));
  expect(await screen.findByText("Sources")).toBeInTheDocument();
  expect(screen.getByText(/mining.md, lines 95/)).toBeInTheDocument();
  expect(screen.getByText("Revenue $152.6M")).toBeInTheDocument();
});

it("creates a blank conversation", async () => {
  vi.mocked(api.askQuestion).mockResolvedValue(answeredResponse);
  render(<App />);
  fireEvent.change(screen.getByLabelText("Research question"), { target: { value: "Question" } });
  fireEvent.click(screen.getByLabelText("Send question"));
  await screen.findByRole("article", { name: "Research answer" });

  fireEvent.click(screen.getByRole("button", { name: /new conversation/i }));
  expect(screen.getByText("Ask a research question")).toBeInTheDocument();
});
