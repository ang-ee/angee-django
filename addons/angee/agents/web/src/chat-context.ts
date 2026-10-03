import { makeContext } from "@angee/ui";

import type { AgentChatView, AgentRosterItem } from "./documents";
import type { AcpSessionNavigation } from "./acp-session";
import type { ReactNode } from "react";

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
  protocolVersion?: 1 | 2;
  onSessionChange?: (id: string) => void;
  /** The sessions page supplies its rail; the transport owns the session operations. */
  renderSessionNavigation?: (sessions: AcpSessionNavigation) => ReactNode;
}

const chatContext = makeContext<AgentChatProps>("AgentChatContext");

export const AgentChatProvider = chatContext.Provider;
/** Read the selected agent and view inside a contributed chat surface. */
export const useAgentChatContext = chatContext.use;
