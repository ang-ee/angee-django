import { describe, expect, test } from "vitest";
import type { AnyMessage, Stream } from "@zed-industries/agent-client-protocol";

import { patchSetModelWireMethod, rewriteBrokenSetModelRequest } from "./acp-transport";

describe("rewriteBrokenSetModelRequest", () => {
  test("rewrites the ACP client library's broken set-model wire method", () => {
    const message = {
      jsonrpc: "2.0",
      id: 2,
      method: "session/set_mode",
      params: { sessionId: "session-1", modelId: "claude-opus-4-8" },
    } satisfies AnyMessage;

    expect(rewriteBrokenSetModelRequest(message)).toEqual({
      jsonrpc: "2.0",
      id: 2,
      method: "session/set_model",
      params: { sessionId: "session-1", modelId: "claude-opus-4-8" },
    });
  });

  test("leaves real set-mode requests alone", () => {
    const message = {
      jsonrpc: "2.0",
      id: 3,
      method: "session/set_mode",
      params: { sessionId: "session-1", modeId: "acceptEdits" },
    } satisfies AnyMessage;

    expect(rewriteBrokenSetModelRequest(message)).toBe(message);
  });

  test("applies the rewrite to outgoing stream writes", async () => {
    const written: AnyMessage[] = [];
    const stream: Stream = {
      readable: new ReadableStream<AnyMessage>(),
      writable: new WritableStream<AnyMessage>({
        write(message) {
          written.push(message);
        },
      }),
    };
    const patched = patchSetModelWireMethod(stream);
    const writer = patched.writable.getWriter();
    try {
      await writer.write({
        jsonrpc: "2.0",
        id: 4,
        method: "session/set_mode",
        params: { sessionId: "session-1", modelId: "claude-opus-4-8" },
      });
    } finally {
      writer.releaseLock();
    }

    expect(written).toHaveLength(1);
    const [message] = written;
    expect(message).toBeDefined();
    expect(message !== undefined && "method" in message && message.method).toBe("session/set_model");
  });
});
