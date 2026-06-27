import * as React from "react";
import { Card, CardContent, EmptyState, buttonVariants, cn, textRoleVariants } from "@angee/ui";
import { useAuthoredMutation } from "@angee/ui";
import { Link } from "@tanstack/react-router";

import { useAgentsT } from "../i18n";
import {
  ResolveSessionForView,
  type AgentChatView,
  type AgentSession,
} from "../documents";
import { AgentChat } from "./AgentChat";
import { KeptAliveAgents, useOpenedAgents } from "./useOpenedAgents";
import { useRunningAgents } from "./useRunningAgents";

/**
 * The side-chatter entry: resolves the agent that serves the user (their running agent) and
 * renders the live ACP chat bound to the open record, so the agent sees what the user is
 * looking at and can read/edit it through its MCP tools. Shows a call-to-action when the user
 * has no running agent. Mount it in a host's chatter "agent" tab with the current
 * `{ resource, recordId }`.
 *
 * The chooser in the chat bar switches between the user's running agents: the effective
 * selection is derived — only the user's explicit pick is state, defaulting to the
 * server-resolved DEFAULT agent (`ResolveSessionForView`) until they pick one — and the opened
 * agents are kept alive (one per-agent runtime each) so switching back restores that agent's
 * own transcript (see `useOpenedAgents`). The live `view` flows on to each `AgentChat`
 * (driving the per-send `<system_context>`).
 */
export function AgentChatterPane({
  resource,
  recordId,
}: {
  resource: string;
  recordId?: string;
}): React.ReactElement {
  const t = useAgentsT();
  const [resolveSession] = useAuthoredMutation(ResolveSessionForView);
  // The shared running-agents owner (same hook the sessions page uses) — live-refreshing,
  // already filtered to the running, non-template agents.
  const { running: agents } = useRunningAgents();

  // Resolved per resource only — the agent doesn't depend on the open record in v1.
  const resolveView = React.useMemo<AgentChatView>(
    () => ({ kind: "dashboard", type: resource }),
    [resource],
  );
  // The live view that tracks the open record, passed to the chat for context.
  const liveView = React.useMemo<AgentChatView>(
    () =>
      recordId !== undefined
        ? { kind: "record", type: resource, sqid: recordId }
        : { kind: "dashboard", type: resource },
    [resource, recordId],
  );

  const [session, setSession] = React.useState<AgentSession | null | "loading">("loading");
  React.useEffect(() => {
    let active = true;
    setSession("loading");
    void resolveSession({ view: resolveView }).then((data) => {
      if (active) setSession(data?.resolve_session_for_view ?? null);
    });
    return () => {
      active = false;
    };
  }, [resolveSession, resolveView]);

  // The effective selection is DERIVED, not effect-mirrored: only the user's explicit pick is
  // state, and until they pick one the selection follows the server-resolved DEFAULT agent.
  // Deriving it (rather than seeding state from `session` in an effect) means the pane never
  // renders a one-frame empty selection the moment the session resolves. Drive this (not a
  // child's `agentId`) so keep-alive never re-runs `useAcpRuntime`'s lossy reset.
  const [pickedId, setPickedId] = React.useState<string | null>(null);
  const sessionAgentId =
    session !== "loading" && session !== null ? session.agent_id : null;
  const selectedId = pickedId ?? sessionAgentId;
  const handleSelect = React.useCallback((id: string) => setPickedId(id), []);
  const { openedIds } = useOpenedAgents({ selectedId });

  if (session === "loading") {
    return <PaneMessage>{t("agents.chat.resolving")}</PaneMessage>;
  }
  if (session === null) {
    return (
      <EmptyState
        icon="agent"
        title={t("agents.agent.noRunningAgent")}
        description={t("agents.agent.chatUnavailable")}
        actions={
          <Link className={buttonVariants({ variant: "primary", size: "sm" })} to="/agents">
            {t("agents.agent.setupAssistant")}
          </Link>
        }
        className="min-h-48 p-4"
      />
    );
  }
  const resolved = session;
  return (
    <KeptAliveAgents
      openedIds={openedIds}
      selectedId={selectedId}
      renderAgent={(id) => (
        <AgentChat
          agentId={id}
          view={liveView}
          modelHandle={agents.find((agent) => agent.id === id)?.model?.name ?? resolved.model_handle}
          agents={agents}
          selectedAgentId={selectedId ?? undefined}
          onSelectAgent={handleSelect}
          fallbackName={resolved.agent_name}
        />
      )}
    />
  );
}

function PaneMessage({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <Card>
      <CardContent>
        <p className={cn(textRoleVariants({ role: "meta" }), "leading-relaxed")}>{children}</p>
      </CardContent>
    </Card>
  );
}
