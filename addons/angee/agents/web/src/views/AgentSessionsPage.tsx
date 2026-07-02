import * as React from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  EmptyState, Glyph, PrimaryPanePublisher, SessionRail, SessionRailItem, Skeleton, StatusDot, buttonVariants, recordPath, statusTone, useRouteRecordId } from "@angee/ui";

import { useAgentsT } from "../i18n";
import { type AgentChatView } from "../documents";
import { AgentChat } from "./AgentChat";
import { KeptAliveAgents, useOpenedAgents } from "./useOpenedAgents";
import { useRunningAgents } from "./useRunningAgents";

const SESSIONS_BASE = "/agents/sessions";

/**
 * The full-page agent sessions view: a left rail of the running agents (the
 * session/thread switcher) and the selected agent's live ACP conversation filling the
 * rest.
 *
 * The rail is published into the shell's primary (left explorer) pane; this
 * component returns only the conversation content. The URL `:id`
 * is the single owner of which agent is shown — the rail rows and the heal-redirect both
 * write it, and the page's parent route stays mounted across `:id` changes, which is the
 * substrate keep-alive needs. Each opened agent renders one stable `<AgentChat>` and only
 * the selected one is shown (the rest hidden but kept alive), so switching back restores
 * that agent's own transcript with zero cross-wiring — the exact keep-alive owners
 * `useOpenedAgents`/`KeptAliveAgents` share with the side-chatter, so the two switchers can
 * never drift.
 */
export function AgentSessionsPage(): React.ReactElement {
  const t = useAgentsT();
  const navigate = useNavigate();
  const selectedId = useRouteRecordId() ?? null;

  // The shared running-agents owner (same hook the side-chatter uses) — live-refreshing,
  // already filtered to the running, non-template agents.
  const { running: agents, loading } = useRunningAgents();

  // Only a RUNNING agent backs a session; a stopped/removed `:id` is absent from the set.
  const selectedRunning = agents.find((agent) => agent.id === selectedId);

  // Heal the URL: no id — or an id that is not a running agent (a deep-link to a stopped or
  // removed one) — falls through to the first running agent, so we never mount an `AgentChat`
  // for an id that would error on `mintEndpoint`.
  React.useEffect(() => {
    const first = agents[0];
    if (first && (!selectedId || !selectedRunning)) {
      void navigate({ to: recordPath(SESSIONS_BASE, first.id), replace: true });
    }
  }, [selectedId, selectedRunning, agents, navigate]);

  // Feed only a valid running id into the keep-alive substrate.
  const activeId = selectedRunning?.id;
  const { openedIds } = useOpenedAgents({ selectedId: activeId });

  // A stable per-agent view envelope (mirrors `AgentChatPanel`): the agent-as-record view
  // that drives the per-send `<system_context>`. Cached per id so its identity never churns.
  const viewCacheRef = React.useRef(new Map<string, AgentChatView>());
  const viewForAgent = React.useCallback((id: string): AgentChatView => {
    const cache = viewCacheRef.current;
    let view = cache.get(id);
    if (!view) {
      view = { kind: "record", type: "agents/agent", sqid: id };
      cache.set(id, view);
    }
    return view;
  }, []);

  // The rail published into the shell primary pane (memoized so it republishes only when the
  // roster / selection changes): skeleton rows while loading, the live switcher once loaded,
  // and nothing in the empty state (the provision call-to-action lives in the content below).
  const rail = React.useMemo<React.ReactNode | null>(() => {
    if (loading) {
      return (
        <SessionRail label={t("sessions.railLabel")} busy>
          {Array.from({ length: 4 }, (_, index) => (
            <li key={index} className="px-2 py-1.5">
              <Skeleton className="h-5" />
            </li>
          ))}
        </SessionRail>
      );
    }
    if (agents.length === 0) {
      return null;
    }
    return (
      <SessionRail
        label={t("sessions.railLabel")}
        action={
          <Link className={buttonVariants({ variant: "ghost", size: "sm" })} to="/agents">
            <Glyph name="plus" />
            {t("sessions.new")}
          </Link>
        }
      >
        {agents.map((agent) => (
          <SessionRailItem
            key={agent.id}
            active={agent.id === selectedId}
            status={
              <StatusDot
                tone={statusTone(agent.runtime_status)}
                label={t("sessions.running")}
              />
            }
            handle={agent.model?.name ?? undefined}
            render={<Link to={recordPath(SESSIONS_BASE, agent.id)} />}
          >
            {agent.name}
          </SessionRailItem>
        ))}
      </SessionRail>
    );
  }, [loading, agents, selectedId, t]);
  // Loading: a skeleton conversation pane beside the skeleton rail rows above.
  if (loading) {
    return (
      <>
        <PrimaryPanePublisher node={rail} />
        <div className="min-w-0 flex-1 p-3">
          <Skeleton className="h-full" />
        </div>
      </>
    );
  }

  // Empty: no running agent → the provision call-to-action (no rail to switch).
  if (agents.length === 0) {
    return (
      <>
        <PrimaryPanePublisher node={rail} />
        <EmptyState
          icon="agent"
          title={t("agent.noRunningAgent")}
          description={t("agent.chatUnavailable")}
          actions={
            <Link className={buttonVariants({ variant: "primary", size: "sm" })} to="/agents">
              {t("agent.setupAssistant")}
            </Link>
          }
          fill
        />
      </>
    );
  }

  // Keep-alive substrate, shared with the side-chatter via `useOpenedAgents` /
  // `KeptAliveAgents`: one stable `<AgentChat>` per opened agent, only the selected
  // shown (the rest hidden via the bare `hidden` attribute, so their `role="status"`
  // live regions are silent). The full-page bar shows the static agent label — the
  // rail is the switcher here, so no in-chat chooser props are passed. The documented
  // fallback (a single `<AgentChat key={activeId}>`) is leak-free too but would drop the
  // prior in-browser transcript on every switch; keep-alive is chosen to avoid that.
  return (
    <>
      <PrimaryPanePublisher node={rail} />
      <div className="min-w-0 flex-1 min-h-0">
        <KeptAliveAgents
          openedIds={openedIds}
          selectedId={activeId}
          renderAgent={(id) => <AgentChat agentId={id} view={viewForAgent(id)} />}
        />
      </div>
    </>
  );
}
