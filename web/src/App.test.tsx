import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import * as client from "./api/client";
import { answer, citation, ingestReport } from "./test/fixtures";

vi.mock("./api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof client>()),
  streamAnswer: vi.fn(),
  askQuestion: vi.fn(),
  listDatasets: vi.fn(),
  listConversations: vi.fn(),
  listRuns: vi.fn(),
  getRunAnswer: vi.fn(),
  checkHealth: vi.fn(),
  getConfig: vi.fn(),
  submitIngest: vi.fn(),
  getIngestReport: vi.fn(),
}));

const dataset = {
  dataset_id: "d1", dataset_version_id: "v1", name: "mcp-financial-data", version_no: 3, status: "ready",
  source_type: "mcp", source_hash: "c".repeat(64), parser_version: "0.1.0",
  created_at: "2026-09-19T12:00:00Z", ready_at: new Date().toISOString(),
  documents: 3, facts: 1840, findings: 7,
};

beforeEach(() => {
  localStorage.clear();
  vi.mocked(client.checkHealth).mockResolvedValue(true);
  vi.mocked(client.listDatasets).mockResolvedValue([dataset]);
  vi.mocked(client.listConversations).mockResolvedValue([]);
  vi.mocked(client.listRuns).mockResolvedValue([]);
  vi.mocked(client.getRunAnswer).mockResolvedValue(undefined);
  vi.mocked(client.getConfig).mockResolvedValue({ mcp_url: null, mcp_tool: null, dataset_name: null });
  vi.mocked(client.submitIngest).mockResolvedValue("job-1");
  vi.mocked(client.getIngestReport).mockResolvedValue(undefined);
  vi.mocked(client.streamAnswer).mockReset();
});

async function ask(question = "Which bank fell on its release?") {
  fireEvent.change(screen.getByLabelText("Research question"), { target: { value: question } });
  fireEvent.click(screen.getByLabelText("Ask question"));
}

it("shows live steps while answering, then the cited answer", async () => {
  let finish: (response: ReturnType<typeof answer>) => void = () => undefined;
  vi.mocked(client.streamAnswer).mockImplementation((_question, _session, handlers) =>
    new Promise((resolve) => {
      handlers?.onStep?.({ seq: 1, kind: "tool_call", name: "find_candidates", detail: "release-day decline" });
      finish = resolve;
    }));
  render(<App />);

  await ask();

  expect(await screen.findByText("find_candidates")).toBeInTheDocument();
  expect(screen.getByText(/Searching the corpus/)).toBeInTheDocument();

  await act(async () => finish(answer()));

  expect(await screen.findByText("Answered")).toBeInTheDocument();
  expect(screen.getByText("Fully supported")).toBeInTheDocument();
  expect(screen.getByText("Sources (1)")).toBeInTheDocument();
  expect(screen.getByText("Research steps · 1")).toBeInTheDocument();
});

it("places a completed answer at its beginning instead of the conversation foot", async () => {
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
  vi.mocked(client.streamAnswer).mockResolvedValue(answer());
  render(<App />);

  await ask();

  await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "smooth" }));
});

it("copies an answer with its supporting citations", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  vi.mocked(client.streamAnswer).mockResolvedValue(answer());
  render(<App />);

  await ask();
  fireEvent.click(await screen.findByRole("button", { name: "Copy answer options" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Copy answer with citations" }));

  await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Sources\n[1] canadian-financials-research.md")));
});

it("closes copy options when the user clicks outside them", async () => {
  vi.mocked(client.streamAnswer).mockResolvedValue(answer());
  render(<App />);

  await ask();
  fireEvent.click(await screen.findByRole("button", { name: "Copy answer options" }));
  expect(screen.getByRole("menuitem", { name: "Copy answer" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close copy options" }));
  expect(screen.queryByRole("menuitem", { name: "Copy answer" })).not.toBeInTheDocument();
});

it("opens a saved conversation from history", async () => {
  vi.mocked(client.listConversations).mockResolvedValue([{
    session_id: "earlier-session", title: "What changed in the quarter?", run_count: 2, updated_at: new Date().toISOString(),
  }]);
  vi.mocked(client.listRuns).mockImplementation((sessionId) => Promise.resolve(sessionId === "earlier-session" ? [{
    run_id: "11111111-1111-1111-1111-111111111111", dataset_version_id: "v1", question: "What changed in the quarter?",
    status: "answered", created_at: "2026-09-19T12:00:00Z",
  }] : []));
  vi.mocked(client.getRunAnswer).mockResolvedValue(answer());
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: /History/ }));
  fireEvent.click(await screen.findByRole("menuitem", { name: /What changed in the quarter/ }));

  await waitFor(() => expect(client.listRuns).toHaveBeenCalledWith("earlier-session", 20, expect.any(AbortSignal)));
  expect(await screen.findByText("What changed in the quarter?")).toBeInTheDocument();
});

it("closes conversation history when the user clicks outside the dropdown", async () => {
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: /History/ }));
  expect(await screen.findByRole("menu", { name: "Recent conversations" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close conversation history" }));
  expect(screen.queryByRole("menu", { name: "Recent conversations" })).not.toBeInTheDocument();
});

it("opens the exact source text from a marker in the prose", async () => {
  vi.mocked(client.streamAnswer).mockResolvedValue(answer());
  render(<App />);

  await ask();
  expect(screen.queryByRole("complementary", { name: "Source viewer" })).not.toBeInTheDocument();
  await screen.findByRole("button", { name: "Show source 1" });
  // the conversation subtree is replaced while history settles, so query again at click time
  fireEvent.click(screen.getByRole("button", { name: "Show source 1" }));

  const panel = screen.getByRole("complementary", { name: "Source viewer" });
  expect(within(panel).getByText("Source 1")).toBeInTheDocument();
  expect(within(panel).getByText(/NA.TO shares fell 5.83%/)).toBeInTheDocument();
  expect(within(panel).getByText("1631–1631")).toBeInTheDocument();
});

it("presents a declined answer as a result, with its reason", async () => {
  vi.mocked(client.streamAnswer).mockResolvedValue(answer({
    status: "declined",
    decline_reason: "future_data",
    evidence_status: "unsupported",
    answer: "The corpus stops before that quarter was reported.",
    citations: [],
  }));
  render(<App />);

  await ask("What will RY report for Q4 FY2026?");

  expect(await screen.findByText("Declined")).toBeInTheDocument();
  expect(screen.getByText("That period is past the corpus cutoff")).toBeInTheDocument();
  expect(screen.getByText("Unsupported")).toBeInTheDocument();
});

it("shows both sides of a conflict", async () => {
  vi.mocked(client.streamAnswer).mockResolvedValue(answer({
    status: "conflict",
    evidence_status: "conflicting",
    citations: [citation({ citation_id: 1 }), citation({ citation_id: 2 })],
    conflicts: [{
      rule: "duplicate_claim",
      explanation: "The report gives two different first quarters above $100B.",
      claims: [
        { text: "Q4 2025 was the first quarter above $100B.", citation_ids: [1] },
        { text: "Q1 2026 crosses $100B.", citation_ids: [2] },
      ],
    }],
  }));
  render(<App />);

  await ask("When did GMV first pass $100B?");

  expect(await screen.findByText("Sources disagree")).toBeInTheDocument();
  const conflict = screen.getByRole("region", { name: "Conflicting evidence" });
  expect(within(conflict).getByText("Duplicate claim, different values")).toBeInTheDocument();
  expect(screen.getByText("Q4 2025 was the first quarter above $100B.")).toBeInTheDocument();
  expect(screen.getByText("Q1 2026 crosses $100B.")).toBeInTheDocument();
});

it("lists what a partial answer leaves out", async () => {
  vi.mocked(client.streamAnswer).mockResolvedValue(answer({
    status: "partial",
    evidence_status: "partial_support",
    limitations: ["No source covers the second half of the question."],
  }));
  render(<App />);

  await ask();

  expect(await screen.findByText("Partial answer")).toBeInTheDocument();
  expect(screen.getByText("No source covers the second half of the question.")).toBeInTheDocument();
});

it("explains a busy server and offers the question again", async () => {
  vi.mocked(client.streamAnswer)
    .mockRejectedValueOnce(new client.BusyError("busy answering other questions", 5))
    .mockResolvedValueOnce(answer());
  render(<App />);

  await ask();

  expect(await screen.findByText("The desk is at capacity")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Ask it again/ }));

  expect(await screen.findByText("Answered")).toBeInTheDocument();
  expect(screen.queryByText("The desk is at capacity")).not.toBeInTheDocument();
});

it("restores the conversation for this session after a reload", async () => {
  vi.mocked(client.listRuns).mockResolvedValue([{
    run_id: "11111111-1111-1111-1111-111111111111",
    dataset_version_id: "v1",
    question: "Which bank fell on its release?",
    status: "answered",
    created_at: "2026-09-19T12:00:00Z",
  }]);
  vi.mocked(client.getRunAnswer).mockResolvedValue(answer());
  render(<App />);

  expect(await screen.findByText("Which bank fell on its release?")).toBeInTheDocument();
  expect(await screen.findByText("Answered")).toBeInTheDocument();
});

it("names the dataset that answers, and how fresh it is", async () => {
  render(<App />);

  expect(await screen.findByText("mcp-financial-data")).toBeInTheDocument();
  expect(screen.getByText("v3")).toBeInTheDocument();
  expect(screen.getByText(/Updated/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("Research service online")).toBeInTheDocument());
});

it("offers the benchmark questions for the loaded corpus and loads one into the composer", async () => {
  render(<App />);

  expect(await screen.findByText("RBC benchmark set")).toBeInTheDocument();
  expect(screen.getByText("30 questions")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Mining" }));
  const mining = screen.getByText(/Which major gold producer announced a transformative North American asset separation/);
  fireEvent.click(mining);

  expect((screen.getByLabelText("Research question") as HTMLTextAreaElement).value)
    .toContain("Which major gold producer");
});

it("lets the user collapse and reopen the question sidebar", async () => {
  render(<App />);

  expect(await screen.findByRole("region", { name: "Question library" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Hide questions" }));
  expect(screen.queryByRole("region", { name: "Question library" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Questions" }));
  expect(screen.getByRole("region", { name: "Question library" })).toBeInTheDocument();
});

it("starts a new research conversation from the Sources view", async () => {
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Sources" }));
  fireEvent.click(screen.getByRole("button", { name: "New conversation" }));

  expect(screen.getByRole("button", { name: "Research" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByLabelText("Research question")).toHaveValue("");
});

it("offers the benchmark questions whatever corpus is loaded, saying which one they were written for", async () => {
  vi.mocked(client.listDatasets).mockResolvedValue([{ ...dataset, name: "surprise-industry-corpus" }]);
  render(<App />);

  // the set belongs to the benchmark, not to a dataset version: judging day serves the same shape
  expect(await screen.findByRole("tab", { name: "Financials" })).toBeInTheDocument();
  expect(screen.getByText(/Which Big 6 Canadian bank crushed consensus estimates/)).toBeInTheDocument();
  const library = screen.getByRole("region", { name: "Question library" });
  expect(within(library).getByText(/the loaded corpus is/)).toBeInTheDocument();
  expect(within(library).getByText("surprise-industry-corpus")).toBeInTheDocument();
});

it("queues an ingest and reports what landed once the job finishes", async () => {
  vi.mocked(client.getIngestReport)
    .mockResolvedValueOnce(undefined)
    .mockResolvedValue(ingestReport());
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Sources" }));
  fireEvent.change(screen.getByLabelText("Research data URL"), { target: { value: "https://provider.example/mcp" } });
  fireEvent.click(screen.getByRole("button", { name: "Add source" }));

  await waitFor(() => expect(client.submitIngest).toHaveBeenCalledWith(
    expect.objectContaining({ source: "mcp", dataset_name: "mcp-financial-data", mcp_url: "https://provider.example/mcp" }),
  ));
  expect(await screen.findByText("Ready", {}, { timeout: 4000 })).toBeInTheDocument();
  expect(screen.getByText("canadian-mining-research.md")).toBeInTheDocument();
  expect(screen.getByText("5 validation findings")).toBeInTheDocument();
});

it("asks the dataset the user picked", async () => {
  vi.mocked(client.listDatasets).mockResolvedValue([dataset, { ...dataset, dataset_version_id: "v2", name: "surprise-industry-corpus" }]);
  vi.mocked(client.streamAnswer).mockResolvedValue(answer());
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Sources" }));
  const rows = await screen.findAllByRole("button", { name: "Use for research" });
  fireEvent.click(rows[0]);
  await ask("Which company grew fastest?");

  await waitFor(() => expect(client.streamAnswer).toHaveBeenCalledWith(
    "Which company grew fastest?",
    expect.any(String),
    expect.objectContaining({ dataset: "surprise-industry-corpus" }),
  ));
});
