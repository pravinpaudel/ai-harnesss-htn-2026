import { describe, expect, it } from "vitest";
import { SseDecoder } from "./sse";

describe("SseDecoder", () => {
  it("decodes the run, step and answer frames of one query", () => {
    const decoder = new SseDecoder();
    const events = decoder.push(
      'event: run\ndata: {"run_id": "r1", "dataset_version": "v1"}\n\n' +
        'event: step\ndata: {"seq": 1, "kind": "policy", "name": "route", "detail": "routed as identification"}\n\n' +
        'event: answer\ndata: {"run_id": "r1", "status": "answered"}\n\n',
    );

    expect(events.map((event) => event.type)).toEqual(["run", "step", "answer"]);
    expect(events[1]).toMatchObject({ type: "step", data: { seq: 1, name: "route" } });
  });

  it("holds back a frame split across chunks", () => {
    const decoder = new SseDecoder();

    expect(decoder.push('event: step\ndata: {"seq": 2, "kind": "tool_call",')).toEqual([]);
    const events = decoder.push(' "name": "find_candidates"}\n\n');

    expect(events).toHaveLength(1);
    expect(events[0].data).toMatchObject({ seq: 2, name: "find_candidates" });
  });

  it("ignores keep-alive comments", () => {
    expect(new SseDecoder().push(": keep-alive\n\n")).toEqual([]);
  });

  it("surfaces an error frame rather than dropping the connection", () => {
    const events = new SseDecoder().push('event: error\ndata: {"detail": "engine failed"}\n\n');

    expect(events[0]).toEqual({ type: "error", data: { detail: "engine failed" } });
  });
});
