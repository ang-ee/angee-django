// ACP transport over a WebSocket: bridges a browser `WebSocket` to the ndjson byte
// stream the ACP SDK's `ndJsonStream` consumes. The agent's WebSocket is fronted by
// the operator's central Caddy, which forward-auths the upgrade against a route token;
// browsers cannot set WebSocket headers, so the token rides in the URL query.

import { ndJsonStream, type AnyMessage, type Stream } from "@zed-industries/agent-client-protocol";

/** A live ACP transport: the ndjson `Stream` plus the socket's open/close lifecycle. */
export interface AcpTransport {
  stream: Stream;
  /** Resolves once the socket is open, rejects if it errors/closes first. */
  ready: Promise<void>;
  /** Resolves when the socket closes (clean or not), carrying the close code. */
  closed: Promise<number>;
  close(): void;
}

/** Human-readable meaning for the close codes the edge path produces. */
const CLOSE_MEANINGS: Record<number, string> = {
  1000: "normal",
  1001: "going away",
  1002: "protocol error",
  1005: "no status received",
  1006: "abnormal — TLS/DNS/connection failed before any close frame (no response from the edge)",
  1008: "policy violation — the edge rejected the route token (forward_auth denied)",
  1011: "server error",
  1015: "TLS handshake failed",
};

/** Describe a WebSocket close: the code, its decoded meaning, and any server reason. */
function describeClose(code: number, reason: string): string {
  const meaning = CLOSE_MEANINGS[code] ?? "unexpected close";
  return `closed (${code} — ${meaning})${reason ? `: ${reason}` : ""}`;
}

/**
 * Open a WebSocket to `url?token=<token>` and adapt it to an ACP ndjson `Stream`.
 *
 * The route token is appended as a query parameter (the central Caddy reads it from
 * `?token=` since a browser cannot set the upgrade headers). Incoming text frames
 * become the readable side; writes from the ACP connection are sent as text frames.
 */
export function openAcpTransport(url: string, token: string): AcpTransport {
  const socket = new WebSocket(appendToken(url, token));
  socket.binaryType = "arraybuffer";
  const encoder = new TextEncoder();

  let pushIncoming: ((chunk: Uint8Array) => void) | null = null;
  let closeIncoming: (() => void) | null = null;
  const input = new ReadableStream<Uint8Array>({
    start(controller) {
      pushIncoming = (chunk) => controller.enqueue(chunk);
      closeIncoming = () => {
        try {
          controller.close();
        } catch {
          // already closed — the socket closed twice; ignore.
        }
      };
    },
  });

  const output = new WritableStream<Uint8Array>({
    write(chunk) {
      // The ACP SDK appends the trailing newline per ndjson frame; forward the bytes,
      // copied into a fresh ArrayBuffer-backed view so `send` accepts the BufferSource.
      socket.send(new Uint8Array(chunk));
    },
  });

  let resolveReady: () => void;
  let rejectReady: (reason: unknown) => void;
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  let resolveClosed: (code: number) => void;
  const closed = new Promise<number>((resolve) => {
    resolveClosed = resolve;
  });

  socket.addEventListener("open", () => resolveReady());
  socket.addEventListener("message", (event) => {
    const data = event.data;
    if (typeof data === "string") {
      // `ndJsonStream` delimits records by newline, but the stdio-to-ws bridge sends
      // each ndjson record as a WebSocket frame with the trailing newline stripped —
      // so re-add it, or the parser buffers forever and `initialize` never resolves.
      pushIncoming?.(encoder.encode(data.endsWith("\n") ? data : `${data}\n`));
    } else if (data instanceof ArrayBuffer) {
      pushIncoming?.(new Uint8Array(data));
    }
  });
  socket.addEventListener("error", () => {
    // A browser WebSocket `error` event carries no detail, and the `close` event
    // that always follows it carries the actionable close code — so log the URL
    // here (token redacted; `url` excludes it) and let `close` reject `ready` with
    // the decoded reason rather than masking it with a generic "error".
    console.warn(`agent WebSocket error: ${url}`);
  });
  socket.addEventListener("close", (event) => {
    const reason = describeClose(event.code, event.reason);
    if (event.code !== 1000 && event.code !== 1001) {
      console.error(`agent WebSocket ${reason} — ${url}`);
    }
    rejectReady(new Error(`agent WebSocket ${reason}`));
    closeIncoming?.();
    resolveClosed(event.code);
  });

  return {
    stream: patchSetModelWireMethod(ndJsonStream(output, input)),
    ready,
    closed,
    close: () => socket.close(),
  };
}

/**
 * Work around @zed-industries/agent-client-protocol@0.4.5 sending setSessionModel()
 * over `session/set_mode`. Remove this shim once the dependency emits `session/set_model`.
 */
export function patchSetModelWireMethod(stream: Stream): Stream {
  return {
    readable: stream.readable,
    writable: new WritableStream<AnyMessage>({
      async write(message) {
        const writer = stream.writable.getWriter();
        try {
          await writer.write(rewriteBrokenSetModelRequest(message));
        } finally {
          writer.releaseLock();
        }
      },
      async close() {
        const writer = stream.writable.getWriter();
        try {
          await writer.close();
        } finally {
          writer.releaseLock();
        }
      },
      async abort(reason) {
        const writer = stream.writable.getWriter();
        try {
          await writer.abort(reason);
        } finally {
          writer.releaseLock();
        }
      },
    }),
  };
}

/** Rewrite only the ACP client library's broken set-model request shape. */
export function rewriteBrokenSetModelRequest(message: AnyMessage): AnyMessage {
  if (!("method" in message) || message.method !== "session/set_mode") return message;
  const params = message.params;
  if (!isRecord(params) || typeof params.modelId !== "string" || "modeId" in params) return message;
  return { ...message, method: "session/set_model" };
}

/** Return `url` with `token` appended as a `token` query parameter. */
function appendToken(url: string, token: string): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
