import { makeContext } from "@angee/ui";

import type { AgentChatView, AgentRosterItem } from "./documents";

/** Runtime-specific chat surfaces contributed by the addon owning their transport. */
export const AGENT_CHAT_SLOT = "agents.chat";

/** Shared presentation inputs for the selected agent's chat transport. */
export interface AgentChatProps {
  agentId: string;
  view: AgentChatView;
  modelHandle?: string;
  agents?: readonly AgentRosterItem[];
  selectedAgentId?: string;
  onSelectAgent?: (id: string) => void;
  fallbackName?: string;
  runtimeClass?: string;
  sessionId?: string;
}

const chatContext = makeContext<AgentChatProps>("AgentChatContext");

export const AgentChatProvider = chatContext.Provider;
/** Read the selected agent and view inside a contributed chat surface. */
export const useAgentChatContext = chatContext.use;
