import { afterEach, expect, it, vi } from "vitest";
import { BusyError, askQuestion, getConfig, getRunAnswer, listConversations, streamAnswer } from "./client";
import { answer } from "../test/fixtures";

afterEach(() => vi.unstubAllGlobals());

function streamOf(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
}

function response(init: { ok?: boolean; status?: number; body?: unknown; stream?: ReadableStream<Uint8Array>; headers?: Record<string, string> }) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    body: init.stream,
    headers: { get: (name: string) => init.headers?.[name] ?? null },
    json: () => Promise.resolve(init.body ?? {}),
  };
}

it("sends the question and session id to the query route", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({ body: answer() }));
  vi.stubGlobal("fetch", fetchMock);

  await askQuestion("What was revenue?", "session-1");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:8000/v1/queries?dataset=latest",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ question: "What was revenue?", session_id: "session-1" }) }),
  );
});

it("reads the load-form defaults from the API", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({ body: {
    mcp_url: "https://research.example/mcp", mcp_tool: "financialDataRetrieval", dataset_name: "firm-research",
  } }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getConfig()).resolves.toMatchObject({ dataset_name: "firm-research" });
  expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/v1/config", expect.any(Object));
});

it("lists recent conversations for the history picker", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({ body: [] }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listConversations()).resolves.toEqual([]);
  expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/v1/conversations?limit=20", expect.any(Object));
});

it("reports a bounded-concurrency 503 as busy, with the server's retry delay", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    response({ ok: false, status: 503, body: { detail: "busy answering other questions; retry shortly" }, headers: { "Retry-After": "5" } }),
  ));

  const error = await askQuestion("q", "s").catch((caught: unknown) => caught);

  expect(error).toBeInstanceOf(BusyError);
  expect((error as BusyError).retryAfterSeconds).toBe(5);
});

it("routes a question at the dataset it was asked of", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({ body: answer() }));
  vi.stubGlobal("fetch", fetchMock);

  await askQuestion("q", "s", "surprise-industry-corpus");

  expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/v1/queries?dataset=surprise-industry-corpus");
});

it("streams steps and resolves with the answer frame", async () => {
  const body = answer();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    stream: streamOf([
      'event: run\ndata: {"run_id": "r1", "dataset_version": "v1"}\n\n',
      'event: step\ndata: {"seq": 1, "kind": "policy", "name": "route"}\n\n',
      ": keep-alive\n\n",
      'event: step\ndata: {"seq": 2, "kind": "tool_call", "name": "find_candidates"}\n\n',
      `event: answer\ndata: ${JSON.stringify(body)}\n\n`,
      'event: done\ndata: {"run_id": "r1"}\n\n',
    ]),
  })));
  const steps: number[] = [];

  const result = await streamAnswer("q", "s", { onStep: (step) => steps.push(step.seq) });

  expect(steps).toEqual([1, 2]);
  expect(result.run_id).toBe(body.run_id);
});

it("falls back to the plain query route when the stream cannot be started", async () => {
  const body = answer();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(response({ ok: false, status: 500, body: { detail: "no stream here" } }))
    .mockResolvedValueOnce(response({ body }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await streamAnswer("q", "s");

  expect(fetchMock.mock.calls[1][0]).toBe("http://localhost:8000/v1/queries?dataset=latest");
  expect(result.run_id).toBe(body.run_id);
});

it("re-throws a 503 from the stream route instead of retrying it", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    response({ ok: false, status: 503, body: { detail: "busy" }, headers: { "Retry-After": "7" } }),
  ));

  await expect(streamAnswer("q", "s")).rejects.toBeInstanceOf(BusyError);
});

it("reads a stored run body back, whether it is JSON or a JSON string", async () => {
  const body = answer();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(response({ body: { run: { response: body } } }))
    .mockResolvedValueOnce(response({ body: { run: { response: JSON.stringify(body) } } })));

  await expect(getRunAnswer("r1")).resolves.toMatchObject({ run_id: body.run_id });
  await expect(getRunAnswer("r1")).resolves.toMatchObject({ run_id: body.run_id });
});
