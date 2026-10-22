import { createNamespaceT } from "@angee/ui";

/** The trigger form's message-source section copy. */
export const enWorkflowsMessagingMessages: Record<string, string> = {
  "trigger.messageSource": "Message source",
  "trigger.channel": "Channel",
};

export const useWorkflowsMessagingT = createNamespaceT("workflows_messaging", enWorkflowsMessagingMessages);
