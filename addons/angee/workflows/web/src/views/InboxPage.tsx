import * as React from "react";
import {
  useAuthoredMutation,
  useAuthoredQuery,
  type DocumentVariables,
} from "@angee/refine";
import {
  Badge,
  Button,
  DescriptorFieldControl,
  EmptyState,
  ErrorBanner,
  FieldDescription,
  FieldLabel,
  FieldRoot,
  Glyph,
  LazyBoundary,
  LoadingPanel,
  RowsListView,
  Textarea,
  formSpecInitialValues,
  useDottedPathFieldErrors,
  useFormSpecFields,
  validationErrorMap,
  type DottedPathFieldErrorMap,
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
type DecisionVerb = DocumentVariables<
  typeof DecideWorkflowDecisionDocument
>["verdict"];
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
  const columns = React.useMemo<readonly ListColumn<DecisionRow>[]>(
    () => [
      { field: "workflow", header: t("col.workflow") },
      { field: "step", header: t("col.step") },
      { field: "action", header: t("col.action") },
      { field: "priority", header: t("col.priority"), align: "right" },
      {
        field: "verdict",
        header: t("col.verdict"),
        widget: "statusBadge",
        tone: { PENDING: "warning" },
      },
      { field: "created_at", header: t("col.created") },
    ],
    [t],
  );

  if (decisionsQuery.fetching && !decisionsQuery.data) {
    return <LoadingPanel message={t("inbox.loading")} />;
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(20rem,26rem)] overflow-hidden bg-canvas">
      <RowsListView
        rows={rows}
        columns={columns}
        fetching={decisionsQuery.fetching}
        error={decisionsQuery.error}
        onRowClick={(row) => setSelectedId(row.id)}
        activeRowId={selected?.id ?? null}
        emptyContent={{
          icon: "workflow-inbox",
          title: t("inbox.emptyTitle"),
          description: t("inbox.emptyDescription"),
        }}
        className="border-r border-border-subtle"
      />
      <DecisionResolutionPanel
        key={selected?.id ?? "empty"}
        decision={selected?.raw ?? null}
        onResolved={() => setSelectedId(null)}
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

  if (!decision) {
    return (
      <div className="min-h-0 overflow-auto bg-sheet-1 p-4">
        <EmptyState
          icon="workflow-inbox"
          title={t("inbox.emptyTitle")}
          description={t("inbox.emptyDescription")}
        />
      </div>
    );
  }

  const title = decisionStepTitle(decision);
  const workflow = decisionWorkflowName(decision);

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
            {t("inbox.payload")}
          </h3>
          <JsonBlock value={decision.payload} />
        </section>
        {decision.decision_schema == null ? (
          <JsonDecisionResolution decision={decision} onResolved={onResolved} />
        ) : (
          <LazyBoundary
            pending={null}
            fallback={
              <ErrorBanner description={t("inbox.invalidFormSpec")} />
            }
            resetKey={decision.id}
          >
            <FormSpecDecisionResolution
              decision={decision}
              onResolved={onResolved}
            />
          </LazyBoundary>
        )}
      </div>
    </aside>
  );
}

function FormSpecDecisionResolution({
  decision,
  onResolved,
}: {
  decision: PendingWorkflowDecision;
  onResolved: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const fields = useFormSpecFields(decision.decision_schema);
  const [values, setValues] = React.useState<Record<string, unknown>>(() =>
    formSpecInitialValues(fields, decision.payload),
  );
  const fieldNames = React.useMemo(
    () => fields.map((field) => field.name),
    [fields],
  );
  const validationErrors = useDottedPathFieldErrors(fieldNames);
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useDecisionResolver(onResolved);

  async function resolve(verdict: DecisionVerb): Promise<void> {
    setError(null);
    validationErrors.clear();
    try {
      validationErrors.replace(
        await resolution.resolve(decision.id, verdict, values),
      );
    } catch (mutationError) {
      setError(
        mutationError instanceof Error
          ? mutationError.message
          : t("inbox.actionFailed"),
      );
    }
  }

  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold text-fg-muted">
        {t("inbox.resolution")}
      </h3>
      {fields.map((field) => (
        <DescriptorFieldControl
          key={field.name}
          field={field}
          value={values[field.name]}
          readOnly={field.readOnly || resolution.fetching}
          messages={validationErrors.messagesFor(field.name)}
          onChange={(value) => {
            validationErrors.clearField(field.name);
            setValues((current) => ({ ...current, [field.name]: value }));
          }}
        />
      ))}
      <ErrorBanner
        description={
          error ?? resolution.error?.message ?? validationErrors.formSummary
        }
      />
      <DecisionVerdictButtons
        fetching={resolution.fetching}
        onResolve={resolve}
      />
    </section>
  );
}

function JsonDecisionResolution({
  decision,
  onResolved,
}: {
  decision: PendingWorkflowDecision;
  onResolved: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const payloadId = React.useId();
  const [payload, setPayload] = React.useState("{}");
  const validationErrors = useDottedPathFieldErrors();
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useDecisionResolver(onResolved);
  const validationError = validationErrors.formSummary;

  async function resolve(verdict: DecisionVerb): Promise<void> {
    setError(null);
    validationErrors.clear();
    let parsed: unknown;
    try {
      parsed = parseJsonPayload(payload, t("json.invalid"));
    } catch (parseError) {
      setError(
        parseError instanceof Error
          ? parseError.message
          : t("inbox.actionFailed"),
      );
      return;
    }
    try {
      validationErrors.replace(
        await resolution.resolve(decision.id, verdict, parsed),
      );
    } catch (mutationError) {
      setError(
        mutationError instanceof Error
          ? mutationError.message
          : t("inbox.actionFailed"),
      );
    }
  }

  return (
    <section className="space-y-3">
      <FieldRoot invalid={Boolean(error || validationError)}>
        <FieldLabel htmlFor={payloadId}>
          {t("inbox.resolution")}
        </FieldLabel>
        <Textarea
          id={payloadId}
          rows={8}
          value={payload}
          invalid={Boolean(error || validationError)}
          onChange={(event) => {
            validationErrors.clear();
            setPayload(event.target.value);
          }}
        />
        <FieldDescription>{t("json.label")}</FieldDescription>
      </FieldRoot>
      <ErrorBanner
        description={error ?? resolution.error?.message ?? validationError}
      />
      <DecisionVerdictButtons
        fetching={resolution.fetching}
        onResolve={resolve}
      />
    </section>
  );
}

function DecisionVerdictButtons({
  fetching,
  onResolve,
}: {
  fetching: boolean;
  onResolve: (verdict: DecisionVerb) => void | Promise<void>;
}): React.ReactElement {
  const t = useWorkflowsT();
  return (
    <div className="flex flex-wrap justify-end gap-2">
      <Button
        type="button"
        variant="ghost"
        loading={fetching}
        onClick={() => void onResolve("ESCALATE")}
      >
        <Glyph name="workflow-escalate" />
        {t("inbox.escalate")}
      </Button>
      <Button
        type="button"
        variant="secondary"
        loading={fetching}
        onClick={() => void onResolve("REJECT")}
      >
        <Glyph name="workflow-reject" />
        {t("inbox.reject")}
      </Button>
      <Button
        type="button"
        variant="primary"
        loading={fetching}
        onClick={() => void onResolve("COMPLETE")}
      >
        <Glyph name="workflow-approve" />
        {t("inbox.complete")}
      </Button>
    </div>
  );
}

function useDecisionResolver(onResolved: () => void): {
  resolve: (
    decision: string,
    verdict: DecisionVerb,
    payload: unknown,
  ) => Promise<DottedPathFieldErrorMap>;
  fetching: boolean;
  error: Error | null;
} {
  const t = useWorkflowsT();
  // PendingWorkflowDecisionsDocument is an authored query keyed by DECISION_MODEL.
  const [decide, state] = useAuthoredMutation(DecideWorkflowDecisionDocument, {
    dataProviderName: "public",
    invalidateModels: [DECISION_MODEL],
    shouldInvalidate: (data) => data?.decide.validation_errors == null,
  });
  const resolve = React.useCallback(
    async (
      decision: string,
      verdict: DecisionVerb,
      payload: unknown,
    ): Promise<DottedPathFieldErrorMap> => {
      const data = await decide({ decision, verdict, payload });
      const wireErrors = data?.decide.validation_errors;
      const parsedErrors = validationErrorMap(wireErrors);
      if (wireErrors != null && parsedErrors === null) {
        throw new Error(t("inbox.invalidValidationErrors"));
      }
      const errors = parsedErrors ?? {};
      if (Object.keys(errors).length === 0) onResolved();
      return errors;
    },
    [decide, onResolved, t],
  );
  return { resolve, fetching: state.fetching, error: state.error };
}

function decisionRows(
  decisions: readonly PendingWorkflowDecision[],
): DecisionRow[] {
  return decisions.map((decision) => ({
    id: decision.id,
    workflow: decisionWorkflowName(decision),
    step: decisionStepTitle(decision),
    action: decision.action,
    priority: decision.priority,
    verdict: decision.verdict,
    created_at: decision.created_at,
    raw: decision,
  }));
}

function decisionWorkflowName(decision: PendingWorkflowDecision): string {
  return decision.workflow_name || "Workflow";
}

function decisionStepTitle(decision: PendingWorkflowDecision): string {
  return decision.step_name || decision.action;
}

function parseJsonPayload(value: string, invalidMessage: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return {};
  try {
    return JSON.parse(trimmed) as unknown;
  } catch (error) {
    throw new Error(
      error instanceof Error && error.message
        ? `${invalidMessage}: ${error.message}`
        : invalidMessage,
    );
  }
}
