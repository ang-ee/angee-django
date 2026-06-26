import * as React from "react";
import { Card, CardContent, EmptyState, buttonVariants } from "@angee/ui";
import { useAuthoredMutation } from "@angee/ui";
import { Link } from "@tanstack/react-router";

import { useAgentsT } from "../i18n";
import {
  ResolveSessionForView,
  type AgentChatView,
  type AgentSession,
} from "../documents";
import { AgentChat } from "./AgentChat";

/**
 * The side-chatter entry: resolves the agent that serves the user (their running agent)
 * and renders the live ACP chat bound to the open record, so the agent sees what the user
 * is looking at and can read/edit it through its MCP tools. Shows a call-to-action when
 * the user has no running agent. Mount it in a host's chatter "agent" tab with the
 * current `{ resource, recordId }`.
 *
 * The agent is resolved once per `resource` (v1 ignores the specific record), so navigating
 * between records keeps the session and transcript; only the live `view` flows on to
 * `AgentChat` (driving the per-send `<system_context>`).
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
  return <AgentChat agentId={session.agent_id} view={liveView} modelHandle={session.model_handle} />;
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
