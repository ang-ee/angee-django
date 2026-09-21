import { useAgentChatContext } from "@angee/agents/chat";
import { AgentChatContent } from "@angee/agents/chat/view";

import { useSessionRuntime } from "./useSessionRuntime";

/** Bind workflow-owned persisted sessions to the shared agent chat surface. */
export function SessionAgentChat() {
  const props = useAgentChatContext();
  const runtimeState = useSessionRuntime(props.agentId, props.view, props.sessionId);
  return <AgentChatContent {...props} runtimeState={runtimeState} />;
}
