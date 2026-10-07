// Incremental Server-Sent Events parser for fetch + ReadableStream (EventSource cannot POST).
// Handles frames split across reads, multi-line `data:` fields, comments (heartbeats) and CRLF/CR line
// endings — including a CR at the end of one read whose LF arrives in the next read.

export interface SSEMessage {
  event: string;
  data: string;
}

export function createSSEParser(onMessage: (message: SSEMessage) => void) {
  let buffer = "";

  function normalise(text: string): string {
    // Keep a trailing lone "\r" pending: its "\n" may arrive in the next chunk.
    const pending = text.endsWith("\r") ? "\r" : "";
    const body = pending ? text.slice(0, -1) : text;
    return body.replace(/\r\n?/g, "\n") + pending;
  }

  function dispatch(frame: string) {
    let event = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) onMessage({ event, data: data.join("\n") });
  }

  return {
    feed(chunk: string) {
      buffer = normalise(buffer + chunk);
      let index = buffer.indexOf("\n\n");
      while (index !== -1) {
        dispatch(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        index = buffer.indexOf("\n\n");
      }
    },
    flush() {
      const rest = normalise(buffer).replace(/\r$/, "");
      buffer = "";
      if (rest.trim()) dispatch(rest);
    },
  };
}

/** Read a fetch Response body as SSE, decoding UTF-8 safely across chunk boundaries. */
export async function readSSE(body: ReadableStream<Uint8Array>, onMessage: (message: SSEMessage) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = createSSEParser(onMessage);
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.flush();
  } finally {
    reader.releaseLock();
  }
}
