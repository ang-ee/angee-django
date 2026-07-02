import * as React from "react";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  FieldDescription,
  FieldLabel,
  FieldRoot,
  Glyph,
  LoadingPanel,
  RowsListView,
  Textarea,
  useAuthoredMutation,
  useAuthoredQuery,
  type ListColumn,
} from "@angee/ui";

import {
  DecideWorkflowDecisionDocument,
  PendingWorkflowDecisionsDocument,
  type PendingWorkflowDecision,
} from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { JsonBlock } from "./JsonBlock";

const DECISION_MODEL = "workflows.Decision";
const INBOX_LIMIT = 100;

interface DecisionRow extends Record<string, unknown> {
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
  const decisionsQuery = useAuthoredQuery(
    PendingWorkflowDecisionsDocument,
    { limit: INBOX_LIMIT, offset: 0 },
    { dataProviderName: "public", models: [DECISION_MODEL] },
  );
  const rows = React.useMemo(
    () => decisionRows(decisionsQuery.data?.workflow_decisions ?? []),
    [decisionsQuery.data?.workflow_decisions],
  );
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const selected = rows.find((row) => row.id === selectedId) ?? rows[0] ?? null;
  React.useEffect(() => {
    if (selectedId && rows.some((row) => row.id === selectedId)) return;
    setSelectedId(rows[0]?.id ?? null);
  }, [rows, selectedId]);
  const columns = React.useMemo<readonly ListColumn<DecisionRow>[]>(
    () => [
      { field: "workflow", header: "Workflow" },
      { field: "step", header: "Step" },
      { field: "action", header: "Action" },
      { field: "priority", header: "Priority", align: "right" },
      {
        field: "verdict",
        header: "Verdict",
        widget: "statusBadge",
        tone: { PENDING: "warning" },
      },
      { field: "created_at", header: "Created" },
    ],
    [],
  );

  if (decisionsQuery.fetching && !decisionsQuery.data) {
    return <LoadingPanel message={t("workflows.inbox.loading")} />;
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(20rem,26rem)] overflow-hidden bg-canvas">
      <RowsListView
        rows={rows}
        columns={columns}
        fetching={decisionsQuery.fetching}
        error={decisionsQuery.error}
        onRowClick={(row) => setSelectedId(row.id)}
        emptyState={{
          icon: "workflow-inbox",
          title: t("workflows.inbox.emptyTitle"),
          description: t("workflows.inbox.emptyDescription"),
        }}
        className="border-r border-border-subtle"
      />
      <DecisionResolutionPanel
        decision={selected?.raw ?? null}
        onResolved={() => {
          decisionsQuery.refetch();
          setSelectedId(null);
        }}
      />
    </div>
  );
}

function DecisionResolutionPanel({
  decision,
  onResolved,
}: {
  decision: PendingWorkflowDecision | null;
  onResolved: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const payloadId = React.useId();
  const [payload, setPayload] = React.useState("{}");
  const [error, setError] = React.useState<string | null>(null);
  const [decide, decideState] = useAuthoredMutation(DecideWorkflowDecisionDocument, {
    dataProviderName: "public",
    invalidateModels: [DECISION_MODEL],
  });
  React.useEffect(() => {
    setPayload("{}");
    setError(null);
  }, [decision?.id]);

  if (!decision) {
    return (
      <div className="min-h-0 overflow-auto bg-sheet-1 p-4">
        <EmptyState
          icon="workflow-inbox"
          title={t("workflows.inbox.emptyTitle")}
          description={t("workflows.inbox.emptyDescription")}
        />
      </div>
    );
  }

  const current = decision;

  async function resolve(verdict: "complete" | "reject"): Promise<void> {
    setError(null);
    let parsed: unknown;
    try {
      parsed = parseJsonPayload(payload);
    } catch (parseError) {
      setError(parseError instanceof Error ? parseError.message : t("workflows.inbox.actionFailed"));
      return;
    }
    try {
      await decide({ decision: current.id, verdict, payload: parsed });
      onResolved();
    } catch (mutationError) {
      setError(mutationError instanceof Error ? mutationError.message : t("workflows.inbox.actionFailed"));
    }
  }

  const title = decision.step_run.step?.name ?? decision.action;
  const workflow =
    decision.step_run.step?.workflow.name ??
    decision.step_run.run.workflow.name;

  return (
    <aside className="min-h-0 overflow-auto bg-sheet-1 p-4">
      <div className="space-y-4">
        <div className="space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>
              <p className="mt-1 truncate text-13 text-fg-muted">{workflow}</p>
            </div>
            <Badge tone="warning">{decision.verdict}</Badge>
          </div>
          <div className="text-xs text-fg-muted">
            {decision.action} · {decision.priority}
          </div>
        </div>
        <section className="space-y-2">
          <h3 className="text-xs font-semibold text-fg-muted">
            {t("workflows.inbox.payload")}
          </h3>
          <JsonBlock value={decision.payload} />
        </section>
        <FieldRoot>
          <FieldLabel htmlFor={payloadId}>
            {t("workflows.inbox.resolution")}
          </FieldLabel>
          <Textarea
            id={payloadId}
            rows={8}
            value={payload}
            invalid={Boolean(error)}
            onChange={(event) => setPayload(event.target.value)}
          />
          <FieldDescription>JSON</FieldDescription>
        </FieldRoot>
        <ErrorBanner description={error ?? decideState.error?.message ?? null} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            loading={decideState.fetching}
            onClick={() => void resolve("reject")}
          >
            <Glyph name="workflow-reject" />
            {t("workflows.inbox.reject")}
          </Button>
          <Button
            type="button"
            variant="primary"
            loading={decideState.fetching}
            onClick={() => void resolve("complete")}
          >
            <Glyph name="workflow-approve" />
            {t("workflows.inbox.complete")}
          </Button>
        </div>
      </div>
    </aside>
  );
}

function decisionRows(
  decisions: readonly PendingWorkflowDecision[],
): DecisionRow[] {
  return decisions.map((decision) => ({
    id: decision.id,
    workflow:
      decision.step_run.step?.workflow.name ??
      decision.step_run.run.workflow.name,
    step: decision.step_run.step?.name ?? decision.step_run.id,
    action: decision.action,
    priority: decision.priority,
    verdict: decision.verdict,
    created_at: decision.created_at,
    raw: decision,
  }));
}

function parseJsonPayload(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return {};
  try {
    return JSON.parse(trimmed) as unknown;
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : "Invalid JSON");
  }
}
