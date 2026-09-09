import { AGENT_SESSION_SLOT, type AgentSessionContributionContext } from "@angee/agents";
import { defineBaseAddon } from "@angee/app";

import { SessionApprovals } from "./SessionApprovals";

const workflowsAgents = defineBaseAddon({
  id: "workflows-agents",
  slots: [
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
