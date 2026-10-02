// Test agent over the SDK's native ndjson streams. Clients and wire validation stay real.
import * as v1 from "@agentclientprotocol/sdk";
import * as v2 from "@agentclientprotocol/sdk/experimental/v2";

import type { AcpTransport } from "./acp-transport";

interface FakeSession {
  id: string;
  title: string;
  cwd: string;
  v1History: v1.SessionNotification[];
  v2History: v2.UpdateSessionNotification[];
  state: "idle" | "running" | "requires_action";
  messageIds: string[];
  updatedAt: string;
  stopReason?: string;
  finishPrompt?: (response: v1.PromptResponse) => void;
}

interface Peer {
  sessionId?: string;
  sendV1?: (note: v1.SessionNotification) => Promise<void>;
  sendV2?: (note: v2.UpdateSessionNotification) => Promise<void>;
  ask: (sessionId: string) => Promise<v1.RequestPermissionResponse | v2.RequestPermissionResponse>;
  close: () => void;
}

const choices: v1.PermissionOption[] = [
  { optionId: "approve", name: "Approve", kind: "allow_once" },
  { optionId: "reject", name: "Reject", kind: "reject_once" },
];

export class FakeAcpAgent {
  readonly sessions = new Map<string, FakeSession>();
  readonly peers = new Set<Peer>();
  readonly restored: Array<{ method: string; sessionId: string; replayFrom?: v2.ReplayFrom | null }> = [];
  readonly permissions: Array<v1.RequestPermissionResponse | v2.RequestPermissionResponse> = [];
  readonly opened: AcpTransport[] = [];
  readonly initialized: number[] = [];
  listCalls = 0;
  promptCalls = 0;
  echoBeforeResponse = true;
  stateBeforeResponse = true;
  failPrompt = false;
  repeatCursor = false;
  private turn = 0;

  constructor(readonly version: 1 | 2, readonly capabilities = { list: true, load: true, resume: false }) {}

  create(title = "New conversation", cwd = "/workspace"): string {
    const id = `s-${this.sessions.size + 1}`;
    this.sessions.set(id, { id, title, cwd, v1History: [], v2History: [], state: "idle", messageIds: [], updatedAt: "2026-10-02T12:00:00Z" });
    return id;
  }

  open = (_url: string, _token: string | null | undefined, version: 1 | 2): AcpTransport => {
    const toAgent = new TransformStream<Uint8Array>();
    const toClient = new TransformStream<Uint8Array>();
    let resolveClosed: (code: number) => void = () => undefined;
    const closed = new Promise<number>((resolve) => { resolveClosed = resolve; });
    const peer: Peer = { ask: async () => ({ outcome: { outcome: "cancelled" } }), close: () => undefined };
    this.peers.add(peer);
    const close = () => { this.peers.delete(peer); peer.close(); resolveClosed(1000); };
    const lifecycle = { ready: Promise.resolve(), closed, close };
    let transport: AcpTransport;
    if (version === 2) {
      const connection = v2.agent({ name: "test-agent" })
        .onRequest(v2.methods.agent.initialize, ({ params }) => {
          this.initialized.push(params.protocolVersion);
          return { protocolVersion: 2, info: { name: "test-agent", version: "1" }, capabilities: { session: {} } };
        })
        .onRequest(v2.methods.agent.session.new, () => {
          const sessionId = this.create(); peer.sessionId = sessionId; return { sessionId };
        })
        .onRequest(v2.methods.agent.session.resume, async ({ params, client }) => {
          this.restored.push({ method: "session/resume", sessionId: params.sessionId, replayFrom: params.replayFrom });
          peer.sessionId = params.sessionId;
          const session = this.get(params.sessionId);
          if (params.replayFrom?.type === "start") {
            for (const note of session.v2History) await client.notify(v2.methods.client.session.update, note);
          }
          await client.notify(v2.methods.client.session.update, { sessionId: session.id, update: {
            sessionUpdate: "state_update", state: session.state, ...(session.stopReason ? { stopReason: session.stopReason } : {}),
          } });
          return {};
        })
        .onRequest(v2.methods.agent.session.list, ({ params }) => this.list(params.cursor))
        .onRequest(v2.methods.agent.session.prompt, async ({ params }) => {
          const session = this.get(params.sessionId);
          peer.sessionId = session.id;
          const messageId = `m-${++this.turn}`;
          this.promptCalls += 1;
          if (this.failPrompt) throw new Error("Test prompt rejected");
          const startsWork = session.messageIds.length === 0 && session.state === "idle";
          session.messageIds.push(messageId);
          const note: v2.UpdateSessionNotification = { sessionId: session.id, update: {
            sessionUpdate: "user_message", messageId, content: params.prompt,
          } };
          const accept = async () => {
            await this.emitV2(note);
            if (startsWork && this.stateBeforeResponse) await this.state(session.id, "running");
          };
          if (this.echoBeforeResponse) await accept();
          else setTimeout(() => { void accept(); }, 10);
          // Queued insertion does not start another bracket. Tests can delay work explicitly.
          return { messageId };
        })
        .onNotification(v2.methods.agent.session.cancel, ({ params }) => this.finish(params.sessionId, "cancelled"))
        .connect(v2.ndJsonStream(toClient.writable, toAgent.readable));
      peer.close = () => connection.close();
      peer.sendV2 = (note) => connection.client.notify(v2.methods.client.session.update, note);
      peer.ask = (sessionId) => connection.client.request(v2.methods.client.session.requestPermission, {
        sessionId, title: "Allow the tool?", options: choices,
        subject: { type: "tool_call", toolCall: { toolCallId: "tool-1", title: "Update record", rawInput: { id: "record-1" } } },
      });
      transport = { ...lifecycle, protocolVersion: 2, stream: v2.ndJsonStream(toAgent.writable, toClient.readable) };
    } else {
      const restore = async (sessionId: string, client: v1.AgentContext, method: string) => {
        peer.sessionId = sessionId;
        this.restored.push({ method, sessionId });
        if (method === "session/load") for (const note of this.get(sessionId).v1History) await client.notify(v1.methods.client.session.update, note);
        return {};
      };
      const connection = v1.agent({ name: "test-agent" })
        .onRequest(v1.methods.agent.initialize, ({ params }) => {
          this.initialized.push(params.protocolVersion);
          return { protocolVersion: 1, agentCapabilities: {
            loadSession: this.capabilities.load,
            sessionCapabilities: { ...(this.capabilities.list ? { list: {} } : {}), ...(this.capabilities.resume ? { resume: {} } : {}) },
          } };
        })
        .onRequest(v1.methods.agent.session.new, () => {
          const sessionId = this.create(); peer.sessionId = sessionId; return { sessionId };
        })
        .onRequest(v1.methods.agent.session.load, ({ params, client }) => restore(params.sessionId, client, "session/load"))
        .onRequest(v1.methods.agent.session.resume, ({ params, client }) => restore(params.sessionId, client, "session/resume"))
        .onRequest(v1.methods.agent.session.list, ({ params }) => this.list(params.cursor))
        .onRequest(v1.methods.agent.session.prompt, ({ params }) => {
          this.promptCalls += 1;
          if (this.failPrompt) throw new Error("Test prompt rejected");
          const session = this.get(params.sessionId);
          session.state = "running";
          peer.sessionId = session.id;
          for (const content of params.prompt) session.v1History.push({ sessionId: session.id, update: { sessionUpdate: "user_message_chunk", content } });
          return new Promise<v1.PromptResponse>((resolve) => { session.finishPrompt = resolve; });
        })
        .onNotification(v1.methods.agent.session.cancel, ({ params }) => this.finish(params.sessionId, "cancelled"))
        .connect(v1.ndJsonStream(toClient.writable, toAgent.readable));
      peer.close = () => connection.close();
      peer.sendV1 = (note) => connection.client.notify(v1.methods.client.session.update, note);
      peer.ask = (sessionId) => connection.client.request(v1.methods.client.session.requestPermission, {
        sessionId, toolCall: { toolCallId: "tool-1", title: "Allow the tool?", rawInput: { id: "record-1" } }, options: choices,
      });
      transport = { ...lifecycle, protocolVersion: 1, stream: v1.ndJsonStream(toAgent.writable, toClient.readable) };
    }
    this.opened.push(transport);
    return transport;
  };

  async chunk(sessionId: string, text: string): Promise<void> {
    const history = this.get(sessionId).v2History;
    const last = history.at(-1)?.update;
    const messageId = last && v2.SessionUpdate.isAgentMessageChunk(last) ? last.messageId : `segment-${history.length}`;
    await this.emitV2({ sessionId, update: { sessionUpdate: "agent_message_chunk", messageId, content: { type: "text", text } } });
    const note: v1.SessionNotification = { sessionId, update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } };
    this.get(sessionId).v1History.push(note);
    await Promise.all([...this.peers].filter((peer) => peer.sessionId === sessionId).map((peer) => peer.sendV1?.(note)));
  }

  async update(sessionId: string, update: v2.SessionUpdate): Promise<void> {
    await this.emitV2({ sessionId, update });
  }

  async state(sessionId: string, state: FakeSession["state"], stopReason?: string): Promise<void> {
    const session = this.get(sessionId);
    session.state = state;
    session.stopReason = stopReason;
    await this.emitV2({ sessionId, update: { sessionUpdate: "state_update", state, ...(stopReason ? { stopReason } : {}) } });
  }

  async finish(sessionId: string, stopReason: v1.StopReason | "_angee/failed" = "end_turn", errorText: string | null = "Stored readable error"): Promise<void> {
    if (this.version === 2 && stopReason === "_angee/failed" && errorText !== null) {
      await this.emitV2({ sessionId, update: { sessionUpdate: "agent_message", messageId: `error-${this.get(sessionId).v2History.length}`, content: [{ type: "text", text: errorText }] } });
    }
    await this.state(sessionId, "idle", stopReason);
    this.get(sessionId).finishPrompt?.({ stopReason: stopReason === "_angee/failed" ? "refusal" : stopReason });
    this.get(sessionId).finishPrompt = undefined;
    this.get(sessionId).messageIds.shift();
    if (this.get(sessionId).messageIds.length) await this.state(sessionId, "running");
  }

  async ask(sessionId: string): Promise<void> {
    await this.state(sessionId, "requires_action");
    await Promise.all([...this.peers].filter((peer) => peer.sessionId === sessionId).map(async (peer) => {
      try { this.permissions.push(await peer.ask(sessionId)); } catch { /* A disconnected request belongs to its old socket. */ }
    }));
  }

  async history(title: string, text: string): Promise<string> {
    const id = this.create(title);
    this.get(id).messageIds.push(`history-user-${id}`);
    await this.emitV2({ sessionId: id, update: { sessionUpdate: "user_message", messageId: `history-user-${id}`, content: [{ type: "text", text: title }] } });
    await this.state(id, "running");
    await this.emitV2({ sessionId: id, update: { sessionUpdate: "agent_message", messageId: `history-agent-${id}`, content: [{ type: "text", text }] } });
    this.get(id).v1History.push(
      { sessionId: id, update: { sessionUpdate: "user_message_chunk", content: { type: "text", text: title } } },
      { sessionId: id, update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } },
    );
    await this.finish(id);
    return id;
  }

  close(): void { for (const transport of this.opened) transport.close(); }

  private list(cursor?: string | null): v1.ListSessionsResponse {
    this.listCalls += 1;
    const all = [...this.sessions.values()].reverse();
    const start = Number(cursor ?? 0);
    const sessions = all.slice(start, start + 2).map(({ id, title, cwd, updatedAt }) => ({ sessionId: id, title, cwd, updatedAt }));
    return { sessions, ...(start + 2 < all.length ? { nextCursor: this.repeatCursor && cursor ? cursor : String(start + 2) } : {}) };
  }

  private get(id: string): FakeSession {
    const session = this.sessions.get(id);
    if (!session) throw new Error(`Unknown test session ${id}`);
    return session;
  }

  private async emitV2(note: v2.UpdateSessionNotification): Promise<void> {
    this.get(note.sessionId).v2History.push(note);
    await Promise.all([...this.peers].filter((peer) => peer.sessionId === note.sessionId).map((peer) => peer.sendV2?.(note)));
  }
}
