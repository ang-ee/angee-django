// The SDK owns WebSocket framing for routed tokens and same-origin cookies.
import { createWebSocketStream } from "@agentclientprotocol/sdk/experimental/ws-client";
import type { Stream } from "@agentclientprotocol/sdk";
import type { Stream as StreamV2, AnyWireMessage } from "@agentclientprotocol/sdk/experimental/v2";

interface TransportLifecycle {
  ready: Promise<void>;
  closed: Promise<number>;
  close(): void;
}
export type AcpTransport = TransportLifecycle & (
  | { protocolVersion: 1; stream: Stream }
  | { protocolVersion: 2; stream: StreamV2 }
);

/** The bridge emits one JSON text frame per message and accepts text frames in both versions. */
export function openAcpTransport(url: string, token: string | null | undefined, protocolVersion: 1 | 2): AcpTransport {
  const endpoint = new URL(url, window.location.href);
  endpoint.protocol = endpoint.protocol === "https:" || endpoint.protocol === "wss:" ? "wss:" : "ws:";
  if (token) endpoint.searchParams.set("token", token);
  let socket: WebSocket | undefined;
  let resolveReady: () => void = () => undefined;
  let rejectReady: () => void = () => undefined;
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = () => reject(new Error());
  });
  let resolveClosed: (code: number) => void = () => undefined;
  const closed = new Promise<number>((resolve) => { resolveClosed = resolve; });
  class ObservedWebSocket extends WebSocket {
    constructor(address: string, protocols?: string | string[]) {
      super(address, protocols);
      socket = this;
      this.addEventListener("open", resolveReady, { once: true });
      this.addEventListener("error", rejectReady, { once: true });
      this.addEventListener("close", (event) => {
        rejectReady();
        resolveClosed(event.code);
      }, { once: true });
    }
  }
  const options = { WebSocket: ObservedWebSocket };
  const lifecycle = { ready, closed, close: () => socket?.close() };
  return protocolVersion === 2
    ? { ...lifecycle, protocolVersion, stream: createWebSocketStream<AnyWireMessage>(endpoint.href, options) }
    : { ...lifecycle, protocolVersion, stream: createWebSocketStream(endpoint.href, options) };
}
