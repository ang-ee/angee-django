import * as React from "react";
import { useNavigate, useRouter } from "@tanstack/react-router";
import {
  EmptyState, Glyph, PrimaryPanePublisher, SessionRail, SessionRailItem, Skeleton, StatusDot, TextLink, buttonVariants, useStatusTone, useRouteHref, useRouteRecordId, useRouteSearch, routeSearchParam, updateRouteSearch } from "@angee/ui";

import { useAgentsT } from "../i18n";
import { type AgentChatView } from "../documents";
import type { AcpSessionNavigation } from "../acp-session";
import { AgentChat } from "./AgentChat";
import { AgentSessionsRail } from "./AgentSessionsRail";
import { KeptAliveAgents, useOpenedAgents } from "./useOpenedAgents";
import { useRunningAgents } from "./useRunningAgents";

/**
 * The full-page agent sessions view: shared rails select the running agent and its
 * ACP session; the selected session's replay and live conversation fill the rest.
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
  const statusTone = useStatusTone();
  const t = useAgentsT();
  const navigate = useNavigate();
  const router = useRouter();
  const routeHref = useRouteHref();
  const selectedId = useRouteRecordId() ?? null;
  const search = useRouteSearch();
  const selectedSessionId = routeSearchParam(search, "session") || undefined;
  const navigateSession = React.useCallback((id: string, replace = false) => {
    void navigate({ to: ".", search: updateRouteSearch({ session: id }), replace, state: true });
  }, [navigate]);
  const agentsHref = routeHref("agents.agents");
  const sessionHref = React.useCallback(
    (id: string) => routeHref("agents.session", { id }),
    [routeHref],
  );

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
      void navigate({ to: sessionHref(first.id), search: updateRouteSearch({ session: undefined }), replace: true });
    }
  }, [selectedId, selectedRunning, agents, navigate, sessionHref]);

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
        className="h-auto max-h-[40%] shrink-0"
        action={
          <TextLink className={buttonVariants({ variant: "ghost", size: "sm" })} href={agentsHref}>
            <Glyph name="plus" />
            {t("sessions.new")}
          </TextLink>
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
            render={<TextLink href={router.buildLocation({ to: sessionHref(agent.id), search: updateRouteSearch({ session: undefined })(search) }).href} />}
          >
            {agent.name}
          </SessionRailItem>
        ))}
      </SessionRail>
    );
  }, [agentsHref, loading, agents, selectedId, sessionHref, statusTone, t, router, search]);
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
            <TextLink className={buttonVariants({ variant: "primary", size: "sm" })} href={agentsHref}>
              {t("agent.setupAssistant")}
            </TextLink>
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
          renderAgent={(id) => {
            const runtimeClass = agents.find((agent) => agent.id === id)?.runtime_class;
            return runtimeClass === undefined ? null : (
              <AgentChat
                agentId={id}
                view={viewForAgent(id)}
                runtimeClass={runtimeClass}
                sessionId={id === activeId ? selectedSessionId : undefined}
                onSessionChange={(sessionId) => { if (id === activeId) navigateSession(sessionId); }}
                renderSessionNavigation={(sessions) => id === activeId ? <SessionNavigation rail={rail} sessions={sessions} selectedId={selectedSessionId} navigate={navigateSession} /> : null}
              />
            );
          }}
        />
      </div>
    </>
  );
}

function SessionNavigation({ rail, sessions, selectedId, navigate }: { rail: React.ReactNode; sessions: AcpSessionNavigation; selectedId?: string; navigate: (id: string, replace?: boolean) => void }): React.ReactElement {
  // Dispatch each route selection once; a newly created id precedes its URL update.
  const routedSession = React.useRef<string | undefined>(undefined);
  React.useEffect(() => {
    if (!selectedId && sessions.currentId) navigate(sessions.currentId, true);
    else if (sessions.ready && selectedId && selectedId !== routedSession.current) {
      routedSession.current = selectedId;
      if (selectedId !== sessions.currentId) sessions.select(selectedId, sessions.items.find((item) => item.sessionId === selectedId)?.cwd ?? "/workspace");
    }
  }, [selectedId, sessions.currentId, sessions.ready, sessions.items, sessions.select, navigate]);
  const node = React.useMemo(() => <div className="flex h-full min-h-0 flex-col">
    {rail}
    <AgentSessionsRail sessions={{ ...sessions, select: (id) => navigate(id) }} />
  </div>, [rail, sessions, navigate]);
  return <PrimaryPanePublisher node={node} />;
}
