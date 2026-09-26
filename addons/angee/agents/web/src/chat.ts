/** Shared chat presentation and ACP projections for addon-owned transports. */
export { AGENT_CHAT_SLOT, useAgentChatContext, type AgentChatProps } from "./chat-slot";
export { convertMessage, foldIntoLog, type ChatMessage } from "./acp-log";
export { emptySession, foldIntoSession } from "./acp-session";
export { RenderAgentPrompt, agentChatViewInput, type AgentChatView } from "./documents";
export type { AcpRuntime, AcpStatus } from "./useAcpRuntime";
