import { afterEach, expect, it, vi } from "vitest";
import { askQuestion, checkHealth } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("sends only the question and client session to the default API route", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ run_id: "run-1" }) });
  vi.stubGlobal("fetch", fetchMock);

  await askQuestion("What was revenue?", "session-1");

  expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/v1/queries", expect.objectContaining({
    method: "POST", body: JSON.stringify({ question: "What was revenue?", session_id: "session-1" }),
  }));
});

it("returns an API error detail to the interface", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: () => Promise.resolve({ detail: "Service unavailable" }) }));

  await expect(askQuestion("Question", "session-1")).rejects.toThrow("Service unavailable");
});

it("reports when the health endpoint is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network error")));

  await expect(checkHealth()).resolves.toBe(false);
});
