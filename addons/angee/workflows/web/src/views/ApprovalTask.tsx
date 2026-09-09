import * as React from "react";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import {
  Badge, Button, Collapsible, ErrorBanner, FieldDescription, FieldLabel, FieldRoot,
  Glyph, LabeledDescriptorField, LazyBoundary, Textarea, TextLink, formSpecInitialValues,
  useDottedPathFieldErrors, useFormSpecFields, useRouteHref, validationErrorMap,
  type DottedPathFieldErrorMap,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { DecideWorkflowDecisionDocument, type PendingWorkflowDecision } from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { JsonBlock } from "./JsonBlock";

const DECISION_MODEL = "workflows.Decision";
type ApprovalVerdict = DocumentVariables<typeof DecideWorkflowDecisionDocument>["verdict"];
export interface ApprovalTaskProps {
  approval: PendingWorkflowDecision;
  available?: boolean;
  onBack?: () => void;
  onResolved: () => void;
  reconcile?: (decisionId: string) => Promise<PendingWorkflowDecision | null>;
  onDirtyChange?: (dirty: boolean) => void;
}

/** The workflow-owned approval task, shared by approval and run surfaces. */
export function ApprovalTask({ approval, available = true, onBack, onResolved, reconcile, onDirtyChange }: ApprovalTaskProps): React.ReactElement {
  const t = useWorkflowsT();
  const active = approval.verdict === "PENDING";
  const editable = active && available;
  React.useEffect(() => {
    if (!editable) onDirtyChange?.(false);
    return () => onDirtyChange?.(false);
  }, [approval.id, editable, onDirtyChange]);
  return (
    <aside className="h-full min-h-0 overflow-auto bg-sheet-1 p-4">
      <div className="space-y-4">
        {onBack ? (
          <Button type="button" variant="ghost" onClick={onBack}>
            <Glyph name="chevron-left" />
            {t("inbox.back")}
          </Button>
        ) : null}
        <div className="space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-fg">{approval.step_name || approval.action}</h2>
              <p className="mt-1 text-13 text-fg-muted">{approval.workflow_name || t("inbox.workflowFallback")}</p>
            </div>
            <Badge tone="warning">{approval.verdict}</Badge>
          </div>
        </div>
        {!available ? <ErrorBanner description={t("inbox.decisionUnavailable")} />
          : !active ? <ErrorBanner description={t("inbox.decisionNoLongerPending")} /> : null}
        {approval.decision_schema == null ? (
          <JsonApprovalResolution key={approval.id} approval={approval} active={active} editable={editable} onResolved={onResolved} reconcile={reconcile} onDirtyChange={onDirtyChange} />
        ) : (
          <LazyBoundary pending={null} fallback={<ErrorBanner description={t("inbox.invalidFormSpec")} />} resetKey={approval.id}>
            <FormSpecApprovalResolution key={approval.id} approval={approval} active={active} editable={editable} onResolved={onResolved} reconcile={reconcile} onDirtyChange={onDirtyChange} />
          </LazyBoundary>
        )}
        <Collapsible variant="section">
          <Collapsible.Trigger><Collapsible.Icon />{t("inbox.sourceData")}</Collapsible.Trigger>
          <Collapsible.Panel>
            <div className="mb-2 text-xs text-fg-muted">
              {t("inbox.sourceMetadata")}: {approval.action} · {approval.priority}
            </div>
            <JsonBlock value={approval.payload} />
          </Collapsible.Panel>
        </Collapsible>
        <DecisionSourceLinks approval={approval} />
      </div>
    </aside>
  );
}

function DecisionSourceLinks({ approval }: { approval: PendingWorkflowDecision }): React.ReactElement {
  const t = useWorkflowsT();
  if (!approval.source_run_id) {
    return <div className="text-xs text-fg-muted">{t("inbox.sourceUnavailable")}</div>;
  }
  return <AvailableDecisionSourceLinks approval={approval} sourceRunId={approval.source_run_id} />;
}

function AvailableDecisionSourceLinks({ approval, sourceRunId }: { approval: PendingWorkflowDecision; sourceRunId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const runHref = routeHref("workflows.run", { id: sourceRunId });
  const executionHref = approval.source_execution_id
    ? `${runHref}?execution=${encodeURIComponent(approval.source_execution_id)}`
    : null;
  const attemptHref = executionHref && approval.source_attempt_id
    ? `${executionHref}&attempt=${encodeURIComponent(approval.source_attempt_id)}`
    : null;
  return (
    <div className="flex flex-wrap gap-x-2 text-xs text-fg-muted">
      <TextLink href={runHref} onNavigate={(href) => { void navigate({ to: href }); }}>
        {t("inbox.openSourceRun")}
      </TextLink>
      {executionHref ? <TextLink href={executionHref} onNavigate={(href) => { void navigate({ to: href }); }}>{t("inbox.sourceExecution", { id: approval.source_execution_id ?? "" })}</TextLink> : null}
      {attemptHref ? <TextLink href={attemptHref} onNavigate={(href) => { void navigate({ to: href }); }}>{t("inbox.sourceAttempt", { id: approval.source_attempt_id ?? "" })}</TextLink> : null}
    </div>
  );
}

type ReconcileApproval = ApprovalTaskProps["reconcile"];

function FormSpecApprovalResolution({ approval, active, editable, onResolved, reconcile, onDirtyChange }: {
  approval: PendingWorkflowDecision; active: boolean; editable: boolean; onResolved: () => void; reconcile?: ReconcileApproval; onDirtyChange?: (dirty: boolean) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const fields = useFormSpecFields(approval.decision_schema);
  const [values, setValues] = React.useState<Record<string, unknown>>(() => formSpecInitialValues(fields, active ? approval.payload : approval.resolution));
  React.useEffect(() => {
    if (!active) setValues(formSpecInitialValues(fields, approval.resolution));
  }, [active, approval.resolution, fields]);
  const fieldNames = React.useMemo(() => fields.map((field) => field.name), [fields]);
  const validationErrors = useDottedPathFieldErrors(fieldNames);
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useApprovalResolver(onResolved, reconcile);
  async function resolve(verdict: ApprovalVerdict): Promise<void> {
    setError(null); validationErrors.clear();
    try {
      validationErrors.replace(await resolution.resolve(approval.id, verdict, values));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("inbox.actionFailed"));
    }
  }
  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold text-fg-muted">{t("inbox.yourDecision")}</h3>
      {fields.map((field) => (
        <LabeledDescriptorField key={field.name} field={field} value={values[field.name]}
          readOnly={field.readOnly || !editable || resolution.fetching} messages={validationErrors.messagesFor(field.name)}
          onChange={(value) => { validationErrors.clearField(field.name); onDirtyChange?.(true); setValues((current) => ({ ...current, [field.name]: value })); }} />
      ))}
      <ErrorBanner description={error ?? resolution.error?.message ?? validationErrors.formSummary} />
      {editable ? <ApprovalVerdictButtons fetching={resolution.fetching} onResolve={resolve} /> : null}
    </section>
  );
}

function JsonApprovalResolution({ approval, active, editable, onResolved, reconcile, onDirtyChange }: {
  approval: PendingWorkflowDecision; active: boolean; editable: boolean; onResolved: () => void; reconcile?: ReconcileApproval; onDirtyChange?: (dirty: boolean) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const payloadId = React.useId();
  const [payload, setPayload] = React.useState(() => JSON.stringify(active ? {} : approval.resolution ?? {}, null, 2));
  React.useEffect(() => {
    if (!active) setPayload(JSON.stringify(approval.resolution ?? {}, null, 2));
  }, [active, approval.resolution]);
  const validationErrors = useDottedPathFieldErrors();
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useApprovalResolver(onResolved, reconcile);
  const validationError = validationErrors.formSummary;
  async function resolve(verdict: ApprovalVerdict): Promise<void> {
    setError(null); validationErrors.clear();
    let parsed: unknown;
    try { parsed = parseJsonPayload(payload, t("json.invalid")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("inbox.actionFailed")); return; }
    try { validationErrors.replace(await resolution.resolve(approval.id, verdict, parsed)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("inbox.actionFailed")); }
  }
  return (
    <section className="space-y-3">
      <FieldRoot invalid={Boolean(error || validationError)}>
        <FieldLabel htmlFor={payloadId}>{t("inbox.resolution")}</FieldLabel>
        <Textarea id={payloadId} rows={8} value={payload} readOnly={!editable || resolution.fetching} invalid={Boolean(error || validationError)}
          onChange={(event) => { validationErrors.clear(); onDirtyChange?.(true); setPayload(event.target.value); }} />
        <FieldDescription>{t("json.label")}</FieldDescription>
      </FieldRoot>
      <ErrorBanner description={error ?? resolution.error?.message ?? validationError} />
      {editable ? <ApprovalVerdictButtons fetching={resolution.fetching} onResolve={resolve} /> : null}
    </section>
  );
}

function ApprovalVerdictButtons({ fetching, onResolve }: { fetching: boolean; onResolve: (verdict: ApprovalVerdict) => void | Promise<void> }): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="flex flex-wrap justify-end gap-2">
    <Button type="button" variant="ghost" loading={fetching} onClick={() => void onResolve("ESCALATE")}><Glyph name="workflow-escalate" />{t("inbox.escalate")}</Button>
    <Button type="button" variant="secondary" loading={fetching} onClick={() => void onResolve("REJECT")}><Glyph name="workflow-reject" />{t("inbox.reject")}</Button>
    <Button type="button" variant="primary" loading={fetching} onClick={() => void onResolve("COMPLETE")}><Glyph name="workflow-approve" />{t("inbox.complete")}</Button>
  </div>;
}

function useApprovalResolver(onResolved: () => void, reconcile?: ReconcileApproval): {
  resolve: (approval: string, verdict: ApprovalVerdict, payload: unknown) => Promise<DottedPathFieldErrorMap>;
  fetching: boolean; error: Error | null;
} {
  const t = useWorkflowsT();
  const [decide, state] = useAuthoredMutation(DecideWorkflowDecisionDocument, {
    dataProviderName: "public", invalidateModels: [DECISION_MODEL],
    shouldInvalidate: (data) => data?.decide?.validation_errors == null,
  });
  const ambiguous = React.useRef(false);
  const inFlight = React.useRef(false);
  const [resolving, setResolving] = React.useState(false);
  const resolve = React.useCallback(async (approval: string, verdict: ApprovalVerdict, payload: unknown): Promise<DottedPathFieldErrorMap> => {
    if (inFlight.current) return {};
    inFlight.current = true;
    setResolving(true);
    try {
      if (ambiguous.current) {
        if (!reconcile) throw new Error(t("inbox.reconcileBeforeRetry"));
        const current = await reconcile(approval);
        if (current == null) throw new Error(t("inbox.decisionUnavailable"));
        if (current.id !== approval) throw new Error(t("inbox.invalidResolutionResponse"));
        if (current.verdict !== "PENDING") throw new Error(t("inbox.decisionNoLongerPending"));
        ambiguous.current = false;
      }
      let data: Awaited<ReturnType<typeof decide>>;
      try {
        data = await decide({ decision: approval, verdict, payload });
      } catch (error) {
        ambiguous.current = true;
        throw error;
      }
      const response = data?.decide;
      if (!response) {
        ambiguous.current = true;
        throw new Error(t("inbox.invalidResolutionResponse"));
      }
      const wireErrors = response.validation_errors;
      const parsedErrors = validationErrorMap(wireErrors);
      if (wireErrors != null && parsedErrors === null) {
        ambiguous.current = true;
        throw new Error(t("inbox.invalidValidationErrors"));
      }
      const errors = parsedErrors ?? {};
      if (Object.keys(errors).length === 0) {
        if (!response.decision) {
          ambiguous.current = true;
          throw new Error(t("inbox.invalidResolutionResponse"));
        }
        const expectedVerdict = verdict === "COMPLETE" ? "COMPLETED"
          : verdict === "REJECT" ? "REJECTED" : "ESCALATED";
        if (response.decision.id !== approval || response.decision.verdict !== expectedVerdict) {
          ambiguous.current = true;
          throw new Error(t("inbox.invalidResolutionResponse"));
        }
        onResolved();
      }
      return errors;
    } finally {
      inFlight.current = false;
      setResolving(false);
    }
  }, [decide, onResolved, reconcile, t]);
  return { resolve, fetching: state.fetching || resolving, error: state.error };
}

function parseJsonPayload(value: string, invalidMessage: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return {};
  try { return JSON.parse(trimmed) as unknown; }
  catch (error) { throw new Error(error instanceof Error && error.message ? `${invalidMessage}: ${error.message}` : invalidMessage); }
}
