import { AGENT_SESSION_SLOT, type AgentSessionContributionContext } from "@angee/agents";
import { AGENT_CHAT_SLOT } from "@angee/agents/chat";
import { defineBaseAddon } from "@angee/app";
import { lazy } from "react";

import { enWorkflowsAgentsMessages } from "./i18n";
import { SessionApprovals } from "./SessionApprovals";

const SessionAgentChat = lazy(() =>
  import("./SessionAgentChat").then((module) => ({ default: module.SessionAgentChat })),
);

const workflowsAgents = defineBaseAddon({
  id: "workflows-agents",
  i18n: { workflows_agents: enWorkflowsAgentsMessages },
  slots: [
    {
      slot: AGENT_CHAT_SLOT,
      model: "agents.Agent",
      impl: "pydantic",
      id: "chat",
      content: <SessionAgentChat />,
    },
    {
      slot: AGENT_SESSION_SLOT,
      id: "workflows-agents.approvals",
      sequence: 20,
      content: {
        render: ({ session }: AgentSessionContributionContext) => (
          <SessionApprovals sessionId={session.sqid} />
        ),
      },
    },
  ],
});

export default workflowsAgents;
