import * as React from "react";
import { Card, CardContent, EmptyState, buttonVariants } from "@angee/base";
import { fromRelayGlobalId, useAuthoredMutation } from "@angee/sdk";
import { Link } from "@tanstack/react-router";

import { useAgentsT } from "../i18n";
import {
  RESOLVE_SESSION_FOR_VIEW_MUTATION,
  type AgentChatView,
  type AgentSession,
  type AgentSessionData,
  type ResolveSessionVariables,
} from "../documents";
import { AgentChat } from "./AgentChat";

/**
 * The side-chatter entry: resolves the agent that serves the user (their running agent)
 * and renders the live ACP chat bound to the open record, so the agent sees what the user
 * is looking at and can read/edit it through its MCP tools. Shows a call-to-action when
 * the user has no running agent. Mount it in a host's chatter "agent" tab with the
 * current `{ model, recordId }`.
 *
 * The agent is resolved once per `model` (v1 ignores the specific record), so navigating
 * between records keeps the session and transcript; only the live `view` flows on to
 * `AgentChat` (driving the per-send `<system_context>`).
 */
export function AgentChatterPane({
  model,
  recordId,
}: {
  model: string;
  recordId?: string;
}): React.ReactElement {
  const t = useAgentsT();
  const [resolveSession] = useAuthoredMutation<AgentSessionData, ResolveSessionVariables>(
    RESOLVE_SESSION_FOR_VIEW_MUTATION,
  );

  // Resolved per model only — the agent doesn't depend on the open record in v1.
  const resolveView = React.useMemo<AgentChatView>(
    () => ({ kind: "dashboard", type: model }),
    [model],
  );
  // The live view that tracks the open record, passed to the chat for context.
  const liveView = React.useMemo<AgentChatView>(
    () =>
      recordId !== undefined
        ? { kind: "record", type: model, sqid: fromRelayGlobalId(recordId) }
        : { kind: "dashboard", type: model },
    [model, recordId],
  );

  const [session, setSession] = React.useState<AgentSession | null | "loading">("loading");
  React.useEffect(() => {
    let active = true;
    setSession("loading");
    void resolveSession({ view: resolveView }).then((data) => {
      if (active) setSession(data?.resolveSessionForView ?? null);
    });
    return () => {
      active = false;
    };
  }, [resolveSession, resolveView]);

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
          <Link
            className={buttonVariants({ variant: "primary", size: "sm" })}
            to="/agents"
          >
            {t("agents.agent.setupAssistant")}
          </Link>
        }
        className="min-h-48 p-4"
      />
    );
  }
  return <AgentChat agentId={session.agentId} view={liveView} modelHandle={session.modelHandle} />;
}

function PaneMessage({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <Card>
      <CardContent>
        <p className="text-13 leading-relaxed text-fg-muted">{children}</p>
      </CardContent>
    </Card>
  );
}
