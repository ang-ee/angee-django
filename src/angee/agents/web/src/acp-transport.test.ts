// @vitest-environment happy-dom

import { afterEach, expect, test, vi } from "vitest";
import { openAcpTransport } from "./acp-transport";

class Socket extends EventTarget {
  static opened: Socket[] = [];
  binaryType = "";
  sent: string[] = [];
  readonly url: URL;
  readyState = 0;
  constructor(address: string) { super(); this.url = new URL(address); Socket.opened.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.dispatchEvent(new CloseEvent("close", { code: 1000 })); }
}
afterEach(() => { Socket.opened.splice(0).forEach((socket) => socket.close()); vi.unstubAllGlobals(); });

test("v1 routed transport preserves query parameters and escapes its token", async () => {
  vi.stubGlobal("WebSocket", Socket);
  const transport = openAcpTransport("wss://agent.example/acp?route=chat", "a+b&c", 1);
  const socket = Socket.opened[0];
  expect(socket?.url.toString()).toBe("wss://agent.example/acp?route=chat&token=a%2Bb%26c");
  socket?.dispatchEvent(new Event("open"));
  await transport.ready;
  const writer = transport.stream.writable.getWriter();
  await writer.write({ jsonrpc: "2.0", method: "session/cancel", params: { sessionId: "s1" } });
  expect(socket?.sent[0]).toContain("session/cancel");
  writer.releaseLock();
});

test("v2 same-origin transport omits empty tokens and accepts batch frames without a newline", async () => {
  vi.stubGlobal("WebSocket", Socket);
  const transport = openAcpTransport("/acp/agents/a1/", "", 2);
  const socket = Socket.opened[0];
  expect(socket?.url.host).toBe(window.location.host);
  expect(socket?.url.pathname).toBe("/acp/agents/a1/");
  expect(socket?.url.searchParams.has("token")).toBe(false);
  socket?.dispatchEvent(new Event("open"));
  await transport.ready;
  const batch = [{ jsonrpc: "2.0", method: "session/update", params: { sessionId: "s1", update: { sessionUpdate: "state_update", state: "running" } } }];
  const reader = transport.stream.readable.getReader();
  socket?.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(batch) }));
  expect((await reader.read()).value).toEqual(batch);
  reader.releaseLock();
});
