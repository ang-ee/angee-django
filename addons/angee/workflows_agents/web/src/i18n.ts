import { createNamespaceT } from "@angee/ui";

export const enWorkflowsAgentsMessages: Record<string, string> = {
  "chat.startFailed": "Failed to start the agent session.",
  "chat.messageRejected": "The agent did not accept the message.",
};

export const useWorkflowsAgentsT = createNamespaceT("workflows_agents", enWorkflowsAgentsMessages);
