import * as v1 from "@agentclientprotocol/sdk";
import * as v2 from "@agentclientprotocol/sdk/experimental/v2";

import type { AcpTransport } from "./acp-transport";

export type AcpNotification =
  | { protocolVersion: 1; params: v1.SessionNotification }
  | { protocolVersion: 2; params: v2.UpdateSessionNotification };
export type AcpPermissionRequest = v1.RequestPermissionRequest | v2.RequestPermissionRequest;
export const ACP_META_NAMESPACE = "angee";

export type AcpPromptResult = { messageId: string; stopReason?: never } | { stopReason: v1.StopReason; messageId?: never };

/** The two SDK APIs adapted to the operations consumed by the one chat runtime. */
export interface AcpClient {
  protocolVersion: 1 | 2;
  promptCapabilities: v1.PromptCapabilities;
  canList: boolean;
  canLoad: boolean;
  canRestore: boolean;
  newSession: (request: v1.NewSessionRequest) => Promise<v1.NewSessionResponse>;
  restoreSession: (request: v1.LoadSessionRequest) => Promise<v1.LoadSessionResponse | v2.ResumeSessionResponse>;
  listSessions: (cursor?: string, signal?: AbortSignal) => Promise<v1.ListSessionsResponse>;
  prompt: (request: v1.PromptRequest) => Promise<AcpPromptResult>;
  cancel: (sessionId: string) => Promise<void>;
  setSessionConfigOption: (request: v1.SetSessionConfigOptionRequest) => Promise<unknown>;
  close: () => void;
}

/** Construct the version-specific SDK client; each SDK validates its own wire boundary. */
export async function connectAcp(
  transport: AcpTransport,
  onUpdate: (note: AcpNotification) => void,
  onPermission: (request: AcpPermissionRequest) => Promise<v1.RequestPermissionResponse>,
): Promise<AcpClient> {
  if (transport.protocolVersion === 2) {
    const connection = v2.client({ name: "angee-web" })
      .onNotification(v2.methods.client.session.update, ({ params }) => onUpdate({ protocolVersion: 2, params }))
      .onRequest(v2.methods.client.session.requestPermission, ({ params }) => onPermission(params))
      .connect(transport.stream);
    await transport.ready;
    const init = await connection.agent.request(v2.methods.agent.initialize, {
      protocolVersion: v2.PROTOCOL_VERSION,
      info: { name: "angee-web", version: "0.0.0" },
      capabilities: {},
    });
    const session = init.capabilities?.session;
    const prompt = session?.prompt;
    return {
      protocolVersion: 2,
      promptCapabilities: { image: prompt?.image != null, embeddedContext: prompt?.embeddedContext != null },
      // In v2, session/list and session/resume are baseline session methods.
      canList: session != null,
      canLoad: session != null,
      canRestore: session != null,
      newSession: async (request) => {
        const result = await connection.agent.request(v2.methods.agent.session.new, {
          ...request, mcpServers: toV2Servers(request.mcpServers),
        });
        return { sessionId: result.sessionId, configOptions: modelOptions(result.configOptions) };
      },
      restoreSession: (request) => connection.agent.request(v2.methods.agent.session.resume, {
        ...request, mcpServers: toV2Servers(request.mcpServers), replayFrom: { type: "start" },
      }),
      listSessions: (cursor, signal) => connection.agent.request(v2.methods.agent.session.list, { cursor }, { cancellationSignal: signal }),
      prompt: async (request) => ({ messageId: (await connection.agent.request(v2.methods.agent.session.prompt, request)).messageId }),
      cancel: (sessionId) => connection.agent.notify(v2.methods.agent.session.cancel, { sessionId }),
      setSessionConfigOption: (request) => connection.agent.request(v2.methods.agent.session.setConfigOption, {
        ...request, type: typeof request.value === "boolean" ? "boolean" : "id",
      }),
      close: () => connection.close(),
    };
  }
  const connection = v1.client({ name: "angee-web" })
    .onNotification(v1.methods.client.session.update, ({ params }) => onUpdate({ protocolVersion: 1, params }))
    .onRequest(v1.methods.client.session.requestPermission, ({ params }) => onPermission(params))
    .connect(transport.stream);
  await transport.ready;
  const init = await connection.agent.request(v1.methods.agent.initialize, {
    protocolVersion: v1.PROTOCOL_VERSION, clientCapabilities: {},
  });
  return {
    protocolVersion: 1,
    promptCapabilities: init.agentCapabilities?.promptCapabilities ?? {},
    canList: init.agentCapabilities?.sessionCapabilities?.list != null,
    canLoad: init.agentCapabilities?.loadSession === true,
    canRestore: init.agentCapabilities?.loadSession === true || init.agentCapabilities?.sessionCapabilities?.resume != null,
    newSession: (request) => connection.agent.request(v1.methods.agent.session.new, request),
    restoreSession: (request) => connection.agent.request(
      init.agentCapabilities?.loadSession ? v1.methods.agent.session.load : v1.methods.agent.session.resume,
      request,
    ),
    listSessions: (cursor, signal) => connection.agent.request(v1.methods.agent.session.list, { cursor }, { cancellationSignal: signal }),
    prompt: async (request) => ({ stopReason: (await connection.agent.request(v1.methods.agent.session.prompt, request)).stopReason }),
    cancel: (sessionId) => connection.agent.notify(v1.methods.agent.session.cancel, { sessionId }),
    setSessionConfigOption: (request) => connection.agent.request(v1.methods.agent.session.setConfigOption, request),
    close: () => connection.close(),
  };
}

/** Apply the agent row's model through the SDK's native session config option. */
export async function selectSessionModel(
  connection: { setSessionConfigOption?: (request: v1.SetSessionConfigOptionRequest) => Promise<unknown> },
  session: v1.NewSessionResponse,
  modelHandle: string,
  unavailable: string,
): Promise<void> {
  if (modelHandle === "") return;
  const option = session.configOptions?.find((entry) => entry.category === "model");
  if (!option || option.type !== "select") return;
  const values = option.options.flatMap((entry) => "options" in entry ? entry.options : [entry]);
  const match = values.find((value) => value.value === modelHandle || value.name === modelHandle);
  if (!match || !connection.setSessionConfigOption) throw new Error(unavailable);
  if (option.currentValue === match.value) return;
  await connection.setSessionConfigOption({ sessionId: session.sessionId, configId: option.id, value: match.value });
}

function toV2Servers(servers: v1.McpServer[]): v2.McpServer[] {
  return servers.map((server) => "type" in server ? server : { ...server, type: "stdio" });
}

function modelOptions(options: v2.SessionConfigOption[] | undefined): v1.SessionConfigOption[] {
  return (options ?? []).filter(v2.SessionConfigOption.isSelect).map((option) => ({
    ...option, id: option.configId,
    options: option.options.flatMap((entry) => "options" in entry ? entry.options : [entry]),
  }));
}
