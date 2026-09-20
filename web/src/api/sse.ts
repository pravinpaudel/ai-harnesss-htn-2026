/**
 * Server-sent event decoding for `POST /v1/queries/stream`.
 *
 * EventSource cannot be used: the route is a POST with a JSON body, so the response body is read as
 * a stream and framed here. Frames are separated by a blank line; `: keep-alive` comments arrive
 * after 15 quiet seconds and carry no data.
 */

import type { AnswerResponse, Step } from "./types";

export type StreamEvent =
  | { type: "run"; data: { run_id: string; dataset_version: string } }
  | { type: "step"; data: Step }
  | { type: "answer"; data: AnswerResponse }
  | { type: "done"; data: { run_id?: string } }
  | { type: "error"; data: { detail: string } };

/**
 * Turns a byte stream into events. Chunk boundaries fall anywhere, so partial frames are held back
 * until their blank-line terminator arrives.
 */
export class SseDecoder {
  private buffer = "";

  push(chunk: string): StreamEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: StreamEvent[] = [];
    let split = this.buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = this.buffer.slice(0, split);
      this.buffer = this.buffer.slice(split + 2);
      const event = decodeFrame(frame);
      if (event) events.push(event);
      split = this.buffer.indexOf("\n\n");
    }
    return events;
  }
}

function decodeFrame(frame: string): StreamEvent | null {
  let name = "";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":") || line.trim() === "") continue; // keep-alive comment
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!name) return null;
  let data: unknown = {};
  if (dataLines.length > 0) {
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return { type: "error", data: { detail: "The server sent a step this page could not read." } };
    }
  }
  if (name === "run" || name === "step" || name === "answer" || name === "done" || name === "error") {
    return { type: name, data } as StreamEvent;
  }
  return null;
}

/** Reads a fetch body to completion, yielding each decoded event. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const sse = new SseDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of sse.push(decoder.decode(value, { stream: true }))) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}
