import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Card, CardContent, EmptyState, LazyBoundary, buttonVariants, cn, textRoleVariants } from "@angee/ui";

import type { ChatterView } from "@angee/ui/runtime";
import { Link } from "@tanstack/react-router";

import { useAgentsT } from "../i18n";
import {
  ResolveSessionForView,
  type AgentChatView,
} from "../documents";
import { KeptAliveAgents, useOpenedAgents } from "./useOpenedAgents";
import { useRunningAgents } from "./useRunningAgents";

// The chat surface is the addon's heaviest tree (assistant-ui + streamdown /
// react-markdown). It's referenced eagerly here — the chatter contribution is
// registered at boot — so lazy-mount it through the shared `LazyBoundary`, the way
// the spotlight and lazy widgets do, keeping streamdown out of the boot bundle and
// in its own chunk loaded only when the user opens the agents chatter.
const AgentChat = React.lazy(() =>
  import("./AgentChat").then((module) => ({ default: module.AgentChat })),
);

/**
 * The side-chatter entry: resolves the agent that serves the user (their running agent) and
 * renders the live ACP chat bound to the open record, so the agent sees what the user is
 * looking at and can read/edit it through its MCP tools. Shows a call-to-action when the user
 * has no running agent. The agents addon contributes it as the global chatter "agents"
 * tab, driven by the active page's `view`; back-compat callers may still pass an explicit
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
  view,
}: {
  resource?: string;
  recordId?: string;
  view?: ChatterView | AgentChatView;
}): React.ReactElement {
  const t = useAgentsT();
  // The shared running-agents owner (same hook the sessions page uses) — live-refreshing,
  // already filtered to the running, non-template agents.
  const { running: agents } = useRunningAgents();

  // The live view that tracks the open record, passed to the chat for context. A
  // global chatter contribution drives this through `view`; the back-compat
  // `{ resource, recordId }` callers fall through to the derived envelope.
  const liveView = React.useMemo<AgentChatView>(
    () =>
      normalizeAgentChatView(
        view ??
          (recordId !== undefined
            ? { kind: "record", type: requiredResource(resource), sqid: recordId }
            : { kind: "dashboard", type: requiredResource(resource) }),
      ),
    [recordId, resource, view],
  );
  // Resolved per resource only — the agent doesn't depend on the open record in v1.
  const resolveView = React.useMemo<AgentChatView>(
    () => ({ kind: "dashboard", type: liveView.type }),
    [liveView.type],
  );

  const sessionQuery = useAuthoredQuery(
    ResolveSessionForView,
    { view: resolveView },
    { models: ["agents.Agent"] },
  );
  const session = sessionQuery.data?.resolve_session_for_view ?? null;
  const loading = sessionQuery.fetching && sessionQuery.data === undefined;

  // The effective selection is DERIVED, not effect-mirrored: only the user's explicit pick is
  // state, and until they pick one the selection follows the server-resolved DEFAULT agent.
  // Deriving it (rather than seeding state from `session` in an effect) means the pane never
  // renders a one-frame empty selection the moment the session resolves. Drive this (not a
  // child's `agentId`) so keep-alive never re-runs `useAcpRuntime`'s lossy reset.
  const [pickedId, setPickedId] = React.useState<string | null>(null);
  const sessionAgentId = session?.agent_id ?? null;
  const selectedId = pickedId ?? sessionAgentId;
  const handleSelect = React.useCallback((id: string) => setPickedId(id), []);
  const { openedIds } = useOpenedAgents({ selectedId });

  if (loading) {
    return <PaneMessage>{t("chat.resolving")}</PaneMessage>;
  }
  if (session === null) {
    return (
      <EmptyState
        icon="agent"
        title={t("agent.noRunningAgent")}
        description={t("agent.chatUnavailable")}
        actions={
          <Link className={buttonVariants({ variant: "primary", size: "sm" })} to="/agents">
            {t("agent.setupAssistant")}
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
        <LazyBoundary pending={<PaneMessage>{t("chat.resolving")}</PaneMessage>}>
          <AgentChat
            agentId={id}
            view={liveView}
            modelHandle={agents.find((agent) => agent.id === id)?.model?.name ?? resolved.model_handle}
            agents={agents}
            selectedAgentId={selectedId ?? undefined}
            onSelectAgent={handleSelect}
            fallbackName={resolved.agent_name}
          />
        </LazyBoundary>
      )}
    />
  );
}

function requiredResource(resource: string | undefined): string {
  if (!resource) {
    throw new Error("AgentChatterPane requires either view or resource.");
  }
  return resource;
}

function normalizeAgentChatView(view: ChatterView | AgentChatView): AgentChatView {
  return {
    kind: view.kind,
    type: view.type,
    ...(view.sqid ? { sqid: view.sqid } : {}),
    ...(view.sqids ? { sqids: [...view.sqids] } : {}),
    ...(view.params ? { params: { ...view.params } } : {}),
  };
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
