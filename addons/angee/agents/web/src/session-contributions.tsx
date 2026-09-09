import * as React from "react";
import { useSlot } from "@angee/ui";

/** Persisted agent-session identity exposed to composed session UI. */
export interface AgentSessionRecord {
  type: "agents/agent_session";
  sqid: string;
}

export interface AgentSessionContributionContext {
  session: AgentSessionRecord;
}

/** Content contract for the agents-owned persisted-session contribution slot. */
export interface AgentSessionContribution {
  render: (context: AgentSessionContributionContext) => React.ReactNode;
}

export const AGENT_SESSION_SLOT = "agents.session.content";

export function agentSessionContribution(value: unknown): AgentSessionContribution | null {
  if (typeof value !== "object" || value === null || !("render" in value)) return null;
  const render = value.render;
  return typeof render === "function" ? { render: (context) => render(context) } : null;
}

/** Mount contributed content with lifecycle keyed to the authoritative persisted session. */
export function AgentSessionContributions({ session }: {
  session?: AgentSessionRecord;
}): React.ReactElement | null {
  const entries = useSlot(AGENT_SESSION_SLOT);
  if (!session) return null;
  return (
    <>
      {entries.map((entry) => {
        const contribution = agentSessionContribution(entry.content);
        return contribution ? (
          <React.Fragment key={`${entry.id}:${session.sqid}`}>
            {contribution.render({ session })}
          </React.Fragment>
        ) : null;
      })}
    </>
  );
}
