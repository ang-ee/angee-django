import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  ControlBandProvider,
  EmptyState,
  LoadingPanel,
  RowsListView,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
  useCollapsiblePane,
  useContainerQuery,
  type ListColumn,
} from "@angee/ui";

import {
  PendingWorkflowDecisionsDocument,
  type PendingWorkflowDecision,
} from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { ApprovalTask } from "./ApprovalTask";

const DECISION_MODEL = "workflows.Decision";
const INBOX_LIMIT = 100;
interface ApprovalRow extends Record<string, unknown> {
  id: string;
  workflow: string;
  step: string;
  action: string;
  priority: number;
  verdict: string;
  created_at: string;
  raw: PendingWorkflowDecision;
}

export function InboxPage(): React.ReactElement {
  const t = useWorkflowsT();
  const approvalsQuery = useAuthoredQuery(
    PendingWorkflowDecisionsDocument,
    { limit: INBOX_LIMIT, offset: 0 },
    { dataProviderName: "public", models: [DECISION_MODEL] },
  );
  const rows = React.useMemo(
    () => approvalRows(approvalsQuery.data?.workflow_decisions ?? [], t("inbox.workflowFallback")),
    [approvalsQuery.data?.workflow_decisions, t],
  );
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [showTask, setShowTask] = React.useState(false);
  const selected = rows.find((row) => row.id === selectedId) ?? rows[0] ?? null;
  const [containerRef, wide] = useContainerQuery(760);
  const approvalsPane = useCollapsiblePane();
  const taskPane = useCollapsiblePane();
  const showingTask = showTask && selected != null;
  React.useLayoutEffect(() => {
    if (wide) {
      approvalsPane.expand();
      taskPane.expand();
    } else if (showingTask) {
      approvalsPane.collapse();
      taskPane.expand();
    } else {
      approvalsPane.expand();
      taskPane.collapse();
    }
  }, [
    approvalsPane.collapse,
    approvalsPane.expand,
    approvalsPane.ready,
    showingTask,
    taskPane.collapse,
    taskPane.expand,
    taskPane.ready,
    wide,
  ]);
  const columns = React.useMemo<readonly ListColumn<ApprovalRow>[]>(
    () => [
      { field: "workflow", header: t("col.workflow") },
      { field: "step", header: t("col.step") },
      { field: "priority", header: t("col.priority"), align: "right" },
      { field: "created_at", header: t("col.created") },
    ],
    [t],
  );

  if (approvalsQuery.isFetching && !approvalsQuery.data) {
    return <LoadingPanel message={t("inbox.loading")} />;
  }

  return (
    <SplitPanes
      ref={containerRef}
      autoSave="workflows.approvals"
      persistLayout={wide}
      panelIds={["approvals", "task"]}
      className="h-full min-h-0 bg-canvas"
    >
      <SplitPane
        id="approvals"
        defaultSize={35}
        minSize={25}
        collapsible
        panelRef={approvalsPane.panelRef}
        onResize={approvalsPane.onResize}
        className={!wide && showingTask ? "hidden" : undefined}
      >
        <ControlBandProvider host={undefined}>
          <RowsListView
            rows={rows}
            columns={columns}
            fetching={approvalsQuery.isFetching}
            error={approvalsQuery.error}
            onRowClick={(row) => {
              setSelectedId(row.id);
              setShowTask(true);
            }}
            activeRowId={selected?.id ?? null}
            emptyContent={{
              icon: "workflow-inbox",
              title: t("inbox.emptyTitle"),
              description: t("inbox.emptyDescription"),
            }}
          />
        </ControlBandProvider>
      </SplitPane>
      <SplitPaneHandle className={!wide ? "hidden" : undefined} />
      <SplitPane
        id="task"
        defaultSize={65}
        minSize={40}
        collapsible
        panelRef={taskPane.panelRef}
        onResize={taskPane.onResize}
        className={!wide && !showingTask ? "hidden" : undefined}
      >
        {selected ? (
          <ApprovalTask
            key={selected.id}
            approval={selected.raw}
            onBack={!wide ? () => setShowTask(false) : undefined}
            onResolved={() => {
              setSelectedId(null);
              setShowTask(false);
            }}
          />
        ) : (
          <div className="min-h-0 overflow-auto bg-sheet-1 p-4">
            <EmptyState icon="workflow-inbox" title={t("inbox.emptyTitle")} description={t("inbox.emptyDescription")} />
          </div>
        )}
      </SplitPane>
    </SplitPanes>
  );
}
function approvalRows(
  decisions: readonly PendingWorkflowDecision[],
  workflowFallback: string,
): ApprovalRow[] {
  return decisions.map((decision) => ({
    id: decision.id,
    workflow: decision.workflow_name || workflowFallback,
    step: decision.step_name || decision.action,
    action: decision.action,
    priority: decision.priority,
    verdict: decision.verdict,
    created_at: decision.created_at,
    raw: decision,
  }));
}
