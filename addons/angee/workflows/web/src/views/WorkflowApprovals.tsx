import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Column,
  EmptyState,
  ErrorBanner,
  Field,
  Form,
  List,
  LoadingPanel,
  ResourceList,
  errorMessage,
  useUnsavedChangesNavigationGuard,
  type RecordPanelContext,
} from "@angee/ui";

import { ScopedWorkflowDecisionDocument, TargetedTabWorkflowDecisionDocument, TargetedWorkflowDecisionDocument, WorkflowDecisionDocument, type PendingWorkflowDecision } from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { ApprovalTask } from "./ApprovalTask";

const DECISION_MODEL = "workflows.Decision";

export interface WorkflowApprovalsProps {
  runId?: string;
  executionId?: string;
  attemptId?: string;
  target?: { model: string; id: string; tab?: string };
  includeResolved?: boolean;
  decisionId?: string | null;
  onDecisionChange?: (id: string | null) => void;
  selectedTaskOnly?: boolean;
}

/** One bounded native Decision collection scoped to a Run or exact related record. */
export function WorkflowApprovals({ runId, executionId, attemptId, target, includeResolved = false, decisionId, onDecisionChange, selectedTaskOnly = false }: WorkflowApprovalsProps): React.ReactElement {
  const t = useWorkflowsT();
  const requestedScope = React.useMemo(
    () => ({
      runId,
      executionId,
      attemptId,
      target: target ? { model: target.model, id: target.id, tab: target.tab } : undefined,
    }),
    [attemptId, executionId, runId, target?.id, target?.model, target?.tab],
  );
  const [scope, setScope] = React.useState(requestedScope);
  const [localSelectedId, setLocalSelectedId] = React.useState<string | null>(null);
  const selectedId = decisionId !== undefined ? decisionId : localSelectedId;
  const [taskDirty, setTaskDirty] = React.useState(false);
  const dirtyRef = React.useRef(false);
  dirtyRef.current = taskDirty;
  const requestLeave = useUnsavedChangesNavigationGuard({
    isDirty: taskDirty,
    isDirtyNow: React.useCallback(() => dirtyRef.current, []),
    readOnly: false,
  });
  const scopeKey = `${runId ?? ""}:${executionId ?? ""}:${attemptId ?? ""}:${target?.model ?? ""}:${target?.id ?? ""}:${target?.tab ?? ""}`;
  const previousScope = React.useRef(scopeKey);
  React.useEffect(() => {
    if (previousScope.current === scopeKey) return;
    void requestLeave().then((leave) => {
      if (leave) {
        previousScope.current = scopeKey;
        setTaskDirty(false);
        if (decisionId === undefined) setLocalSelectedId(null);
        onDecisionChange?.(null);
        setScope(requestedScope);
      }
    });
  }, [decisionId, onDecisionChange, requestLeave, requestedScope, scopeKey]);
  const selectDecision = React.useCallback((id: string | null) => {
    if (id === selectedId) return;
    void requestLeave().then((leave) => {
      if (leave) {
        setTaskDirty(false);
        if (decisionId === undefined) setLocalSelectedId(id);
        onDecisionChange?.(id);
      }
    });
  }, [decisionId, onDecisionChange, requestLeave, selectedId]);
  const tabs = React.useMemo(() => [{
    id: "decision",
    label: t("inbox.yourDecision"),
    render: (context: RecordPanelContext) => scope.target
      ? <TargetedDecisionTask key={context.recordId} {...context} target={scope.target} onDirtyChange={setTaskDirty} />
      : scope.runId
        ? <ScopedDecisionTask key={context.recordId} {...context} runId={scope.runId} onDirtyChange={setTaskDirty} />
        : <GlobalDecisionTask key={context.recordId} {...context} onDirtyChange={setTaskDirty} />,
  }], [scope.runId, scope.target?.id, scope.target?.model, scope.target?.tab, t]);
  if (selectedTaskOnly && selectedId && scope.target) {
    return <section aria-label={t("inbox.title")} className="h-full min-h-0">
      <TargetedDecisionTask key={`${scope.target.model}:${scope.target.id}:${scope.target.tab ?? ""}:${selectedId}`} recordId={selectedId} reload={() => undefined} target={scope.target} onDirtyChange={setTaskDirty} />
    </section>;
  }
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
          ...(scope.runId ? { "step_run.run": { exact: scope.runId } } : {}),
          ...(scope.executionId ? { step_run: { exact: scope.executionId } } : {}),
          ...(scope.attemptId ? { suspension_attempt: { exact: scope.attemptId } } : {}),
          ...(scope.target ? { target_model: { exact: scope.target.model }, target_id: { exact: scope.target.id } } : {}),
          ...(scope.target?.tab ? { target_tab: { exact: scope.target.tab } } : {}),
          ...(!includeResolved ? { verdict: { exact: "PENDING" } } : {}),
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

function TargetedDecisionTask({ recordId, reload, target, onDirtyChange }: {
  recordId: string;
  reload: () => void;
  target: { model: string; id: string; tab?: string };
  onDirtyChange: (dirty: boolean) => void;
}): React.ReactElement {
  return target.tab
    ? <TargetedTabDecisionTask recordId={recordId} reload={reload} target={{ ...target, tab: target.tab }} onDirtyChange={onDirtyChange} />
    : <TargetedRecordDecisionTask recordId={recordId} reload={reload} target={target} onDirtyChange={onDirtyChange} />;
}

function TargetedRecordDecisionTask({ recordId, reload, target, onDirtyChange }: {
  recordId: string; reload: () => void; target: { model: string; id: string }; onDirtyChange: (dirty: boolean) => void;
}): React.ReactElement {
  const decision = useAuthoredQuery(
    TargetedWorkflowDecisionDocument,
    { id: recordId, targetModel: target.model, targetId: target.id },
    { dataProviderName: "public", models: [DECISION_MODEL] },
  );
  return <DecisionTaskResult context={{ recordId, reload }} approval={decision.data?.workflow_decisions[0]} onDirtyChange={onDirtyChange}
    fetching={decision.isFetching} error={decision.error} refetch={async () => (await decision.refetch()).data?.workflow_decisions[0] ?? null} />;
}

function TargetedTabDecisionTask({ recordId, reload, target, onDirtyChange }: {
  recordId: string; reload: () => void; target: { model: string; id: string; tab: string }; onDirtyChange: (dirty: boolean) => void;
}): React.ReactElement {
  const decision = useAuthoredQuery(
    TargetedTabWorkflowDecisionDocument,
    { id: recordId, targetModel: target.model, targetId: target.id, targetTab: target.tab },
    { dataProviderName: "public", models: [DECISION_MODEL] },
  );
  return <DecisionTaskResult context={{ recordId, reload }} approval={decision.data?.workflow_decisions[0]} onDirtyChange={onDirtyChange}
    fetching={decision.isFetching} error={decision.error} refetch={async () => (await decision.refetch()).data?.workflow_decisions[0] ?? null} />;
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
  if (error && !retained) return <ErrorBanner description={errorMessage(error, t("inbox.decisionUnavailable"))} />;
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
