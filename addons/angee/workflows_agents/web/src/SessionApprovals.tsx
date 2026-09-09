import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { useWorkflowsT, WorkflowApprovals } from "@angee/workflows";
import { Button, Collapsible, EmptyState, ErrorBanner, errorMessage, LoadingPanel } from "@angee/ui";

import { AgentSessionWorkflowRunDocument } from "./documents";

export function SessionApprovals({ sessionId }: { sessionId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const run = useAuthoredQuery(
    AgentSessionWorkflowRunDocument,
    { session: sessionId },
    { models: ["agents.AgentSession", "workflows.WorkflowRun"] },
  );
  const content = run.isFetching && !run.data
    ? <LoadingPanel message={t("inbox.loading")} />
    : run.error
      ? <div className="space-y-2 p-2"><ErrorBanner description={errorMessage(run.error, t("inbox.sourceUnavailable"))} /><Button type="button" size="sm" variant="secondary" onClick={() => { void run.refetch(); }}>{t("input.retry")}</Button></div>
      : !run.data?.agent_session_workflow_run
        ? <EmptyState icon="workflow-inbox" title={t("inbox.sourceUnavailable")} />
        : <WorkflowApprovals runId={run.data.agent_session_workflow_run} />;
  return (
    <Collapsible variant="section" defaultOpen className="min-h-0 flex-none border-t border-border-subtle bg-sheet-1 px-3 py-1">
      <Collapsible.Trigger><Collapsible.Icon />{t("inbox.title")}</Collapsible.Trigger>
      <Collapsible.Panel className="max-h-[45dvh] min-h-0 overflow-auto pb-2">
        <div className="h-80 max-h-[42dvh] min-h-48">
          {content}
        </div>
      </Collapsible.Panel>
    </Collapsible>
  );
}
