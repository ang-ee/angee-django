import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Column,
  EmptyState,
  Field,
  Form,
  List,
  LoadingPanel,
  ResourceList,
  useUnsavedChangesNavigationGuard,
  type RecordPanelContext,
} from "@angee/ui";

import { ScopedWorkflowDecisionDocument, WorkflowDecisionDocument, type PendingWorkflowDecision } from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { ApprovalTask } from "./ApprovalTask";

const DECISION_MODEL = "workflows.Decision";

/** One bounded native Decision collection scoped to an authoritative workflow Run. */
export function WorkflowApprovals({ runId, executionId, attemptId }: {
  runId?: string;
  executionId?: string;
  attemptId?: string;
}): React.ReactElement {
  const t = useWorkflowsT();
  const requestedScope = React.useMemo(
    () => ({ runId, executionId, attemptId }),
    [attemptId, executionId, runId],
  );
  const [scope, setScope] = React.useState(requestedScope);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [taskDirty, setTaskDirty] = React.useState(false);
  const dirtyRef = React.useRef(false);
  dirtyRef.current = taskDirty;
  const requestLeave = useUnsavedChangesNavigationGuard({
    isDirty: taskDirty,
    isDirtyNow: React.useCallback(() => dirtyRef.current, []),
    readOnly: false,
  });
  const scopeKey = `${runId ?? ""}:${executionId ?? ""}:${attemptId ?? ""}`;
  const previousScope = React.useRef(scopeKey);
  React.useEffect(() => {
    if (previousScope.current === scopeKey) return;
    void requestLeave().then((leave) => {
      if (leave) {
        previousScope.current = scopeKey;
        setTaskDirty(false);
        setSelectedId(null);
        setScope(requestedScope);
      }
    });
  }, [requestLeave, requestedScope, scopeKey]);
  const selectDecision = React.useCallback((id: string | null) => {
    if (id === selectedId) return;
    void requestLeave().then((leave) => {
      if (leave) { setTaskDirty(false); setSelectedId(id); }
    });
  }, [requestLeave, selectedId]);
  const tabs = React.useMemo(() => [{
    id: "decision",
    label: t("inbox.yourDecision"),
    render: (context: RecordPanelContext) => scope.runId
      ? <ScopedDecisionTask key={context.recordId} {...context} runId={scope.runId} onDirtyChange={setTaskDirty} />
      : <GlobalDecisionTask key={context.recordId} {...context} onDirtyChange={setTaskDirty} />,
  }], [scope.runId, t]);
  return (
    <section aria-label={t("inbox.title")} className="h-full min-h-0">
      <ResourceList
        resource={DECISION_MODEL}
        scope="local"
        placement="inline"
        recordId={selectedId}
        onSelect={(id) => selectDecision(id)}
        onClose={() => selectDecision(null)}
        hideCreate
        pageSize={20}
        baseFilter={{
          ...(scope.runId ? { step_run__run: { exact: scope.runId } } : {}),
          ...(scope.executionId ? { step_run: { exact: scope.executionId } } : {}),
          ...(scope.attemptId ? { suspension_attempt: { exact: scope.attemptId } } : {}),
          verdict: { exact: "PENDING" },
        }}
        recordTabs={tabs}
        defaultRecordTab="decision"
        overviewTab={{ label: t("form.details"), position: "last" }}
      >
        <List resource={DECISION_MODEL} order={{ priority: "ASC" }} emptyContent={t("inbox.emptyDescription")}>
          <Column field="action" />
          <Column field="verdict" widget="statusBadge" />
          <Column field="priority" />
          <Column field="updated_at" />
        </List>
        <Form resource={DECISION_MODEL} readOnly>
          <Field name="action" title readOnly />
          <Field name="verdict" widget="statusBadge" readOnly />
          <Field name="priority" readOnly />
          <Field name="updated_at" readOnly />
        </Form>
      </ResourceList>
    </section>
  );
}

function GlobalDecisionTask(context: RecordPanelContext & { onDirtyChange: (dirty: boolean) => void }): React.ReactElement {
  const decision = useAuthoredQuery(
    WorkflowDecisionDocument,
    { id: context.recordId },
    { dataProviderName: "public", models: [DECISION_MODEL] },
  );
  return <DecisionTaskResult context={context} approval={decision.data?.workflow_decisions[0]} onDirtyChange={context.onDirtyChange}
    fetching={decision.isFetching} error={decision.error} refetch={async () => (await decision.refetch()).data?.workflow_decisions[0] ?? null} />;
}

function ScopedDecisionTask({ recordId, reload, runId, onDirtyChange }: RecordPanelContext & { runId: string; onDirtyChange: (dirty: boolean) => void }): React.ReactElement {
  const decision = useAuthoredQuery(
    ScopedWorkflowDecisionDocument,
    { id: recordId, run: runId },
    { dataProviderName: "public", models: [DECISION_MODEL] },
  );
  return <DecisionTaskResult context={{ recordId, reload }} approval={decision.data?.workflow_decisions[0]} onDirtyChange={onDirtyChange}
    fetching={decision.isFetching} error={decision.error} refetch={async () => (await decision.refetch()).data?.workflow_decisions[0] ?? null} />;
}

function DecisionTaskResult({ context, approval, fetching, error, refetch, onDirtyChange }: {
  context: Pick<RecordPanelContext, "recordId" | "reload">;
  approval?: PendingWorkflowDecision;
  fetching: boolean;
  error: unknown;
  refetch: () => Promise<PendingWorkflowDecision | null>;
  onDirtyChange: (dirty: boolean) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const [retained, setRetained] = React.useState(approval);
  React.useEffect(() => {
    if (approval) setRetained(approval);
  }, [approval]);
  if (fetching && !retained) return <LoadingPanel message={t("inbox.loading")} />;
  if (!retained) {
    return <EmptyState icon="workflow-inbox" title={t("inbox.decisionUnavailable")} />;
  }
  return (
    <ApprovalTask
      approval={retained}
      onDirtyChange={onDirtyChange}
      available={!error && Boolean(approval)}
      onResolved={() => {
        onDirtyChange(false);
        void refetch();
        context.reload();
      }}
      reconcile={refetch}
    />
  );
}
