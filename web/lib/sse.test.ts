import { describe, expect, it } from "vitest";

import { createSSEParser, readSSE, type SSEMessage } from "./sse";

function collect(chunks: string[]): SSEMessage[] {
  const out: SSEMessage[] = [];
  const parser = createSSEParser((m) => out.push(m));
  chunks.forEach((c) => parser.feed(c));
  parser.flush();
  return out;
}

describe("SSE parser", () => {
  it("parses complete frames", () => {
    expect(collect(['event: token\ndata: "Hi"\n\nevent: done\ndata: {"ok":true}\n\n'])).toEqual([
      { event: "token", data: '"Hi"' },
      { event: "done", data: '{"ok":true}' },
    ]);
  });

  it("handles frames split across reads at every position", () => {
    const stream = 'event: meta\ndata: {"a":1}\n\nevent: token\ndata: "x"\n\n';
    for (let cut = 1; cut < stream.length; cut++) {
      expect(collect([stream.slice(0, cut), stream.slice(cut)])).toEqual([
        { event: "meta", data: '{"a":1}' },
        { event: "token", data: '"x"' },
      ]);
    }
  });

  it("joins multi-line data fields with newlines", () => {
    expect(collect(["event: token\ndata: line1\ndata: line2\n\n"])).toEqual([{ event: "token", data: "line1\nline2" }]);
  });

  it("handles CRLF, including a CR/LF pair split across reads", () => {
    expect(collect(["event: token\r\ndata: \"a\"\r", "\n\r\n"])).toEqual([{ event: "token", data: '"a"' }]);
    expect(collect(["event: token\rdata: \"b\"\r\r"])).toEqual([{ event: "token", data: '"b"' }]);
  });

  it("ignores heartbeat comments and empty frames", () => {
    expect(collect([": heartbeat\n\n", "\n\n", 'event: done\ndata: {}\n\n'])).toEqual([{ event: "done", data: "{}" }]);
  });

  it("defaults the event name and flushes a trailing frame without blank line", () => {
    expect(collect(["data: plain"])).toEqual([{ event: "message", data: "plain" }]);
  });

  it("reads a ReadableStream and decodes multi-byte UTF-8 split across chunks", async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: "₹1,00,000"\n\n');
    const cut = bytes.indexOf(0xe2) + 1; // split inside the ₹ code point
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut));
        controller.enqueue(bytes.slice(cut));
        controller.close();
      },
    });
    const out: SSEMessage[] = [];
    await readSSE(body, (m) => out.push(m));
    expect(out).toEqual([{ event: "token", data: '"₹1,00,000"' }]);
  });
});
