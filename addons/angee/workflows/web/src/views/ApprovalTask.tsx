import * as React from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import {
  Badge, Button, Collapsible, ErrorBanner, FieldDescription, FieldLabel, FieldRoot,
  Glyph, JsonEditor, JsonValueView, LabeledDescriptorField, LazyBoundary, TextLink, formSpecInitialValues,
  PageAside,
  compileDecisionActionFormSpec, errorMessage, jsonValueFromUnknown, statusTone, useAppRuntime, useConfirm, useDottedPathFieldErrors, useResourceRecordHrefLookup, useRouteHref, validationErrorMap,
  useModelSlot,
  useRecordPeek,
  type DottedPathFieldErrorMap, type FormSpecFieldDescriptor, type JsonValue, type RecordPeekReference,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { decisionHref } from "../decision-navigation";
import { DecideWorkflowDecisionDocument, type PendingWorkflowDecision } from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { WORKFLOW_DECISION_CONTENT_SLOT } from "../slots";

const DECISION_MODEL = "workflows.Decision";
export type ApprovalVerdict = DocumentVariables<typeof DecideWorkflowDecisionDocument>["verdict"];
export type WorkflowDecisionRecordReference = RecordPeekReference;
export interface WorkflowDecisionContentProps {
  approval: PendingWorkflowDecision;
  contextFields: readonly FormSpecFieldDescriptor[];
  contextValues: Readonly<Record<string, unknown>>;
  inputFields: readonly FormSpecFieldDescriptor[];
  values: Readonly<Record<string, unknown>>;
  setValue: (name: string, value: unknown) => void;
  messagesFor: (name: string) => readonly string[];
  editable: boolean;
  fetching: boolean;
  readOnly: boolean;
  openRecord?: (reference: WorkflowDecisionRecordReference) => void;
  openEvidence?: (reference: WorkflowDecisionRecordReference) => void;
}
export interface ApprovalTaskProps {
  approval: PendingWorkflowDecision;
  available?: boolean;
  onBack?: () => void;
  onResolved: () => void | Promise<void>;
  reconcile?: (decisionId: string) => Promise<PendingWorkflowDecision | null>;
  onDirtyChange?: (dirty: boolean) => void;
  onSkip?: () => void;
  onOpenRecord?: WorkflowDecisionContentProps["openRecord"];
  onOpenEvidence?: WorkflowDecisionContentProps["openEvidence"];
}

/** The workflow-owned approval task, shared by approval and run surfaces. */
export function ApprovalTask({ approval, available = true, onBack, onResolved, reconcile, onDirtyChange, onSkip, onOpenRecord, onOpenEvidence }: ApprovalTaskProps): React.ReactElement {
  const t = useWorkflowsT();
  const openRecord = useRecordPeek();
  const active = approval.verdict === "PENDING";
  const editable = active && available;
  const [committedVerdict, setCommittedVerdict] = React.useState<string | null>(null);
  const displayedVerdict = committedVerdict ?? approval.verdict;
  React.useEffect(() => setCommittedVerdict(null), [approval.id]);
  React.useEffect(() => {
    if (!editable) onDirtyChange?.(false);
    return () => onDirtyChange?.(false);
  }, [approval.id, editable, onDirtyChange]);
  return (
    <PageAside collapse="never" gutter="compact" className="h-full w-full bg-sheet-1">
      <div className="space-y-4">
        {onBack || onSkip ? <div className="flex items-center justify-between gap-2">
          {onBack ? (
            <Button type="button" variant="ghost" onClick={onBack}>
              <Glyph name="chevron-left" />
              {t("inbox.back")}
            </Button>
          ) : <span />}
          {onSkip ? <Button type="button" variant="ghost" onClick={onSkip}>{t("inbox.skip")}<Glyph name="chevron-right" /></Button> : null}
        </div> : null}
        <div className="flex items-center justify-between gap-3 text-xs text-fg-muted">
          <p className="min-w-0 truncate">
            {[approval.workflow_name || t("inbox.workflowFallback"), approval.step_name || approval.action]
              .filter(Boolean).join(" · ")}
          </p>
          <Badge tone={statusTone(displayedVerdict)}>{displayedVerdict}</Badge>
        </div>
        {!available ? <ErrorBanner description={t("inbox.decisionUnavailable")} />
          : !active ? <div className="space-y-1 text-sm text-fg-muted">
            <p>{t("inbox.decisionNoLongerPending")}</p>
            <p>{t("inbox.decisionResolvedBy", {
              actor: approval.resolved_by || t("inbox.unknownActor"),
              date: new Date(approval.updated_at).toLocaleString(),
            })}</p>
          </div> : null}
        {approval.decision_schema == null ? (
          <JsonApprovalResolution key={approval.id} approval={approval} active={active} editable={editable} onResolved={onResolved} reconcile={reconcile} onDirtyChange={onDirtyChange} onCommitted={setCommittedVerdict} />
        ) : (
          <LazyBoundary pending={null} fallback={<ErrorBanner description={t("inbox.invalidFormSpec")} />} resetKey={approval.id}>
            <FormSpecApprovalResolution key={approval.id} approval={approval} active={active} editable={editable} onResolved={onResolved} reconcile={reconcile} onDirtyChange={onDirtyChange} onCommitted={setCommittedVerdict} onOpenRecord={onOpenRecord ?? openRecord} onOpenEvidence={onOpenEvidence ?? openRecord} />
          </LazyBoundary>
        )}
        <Collapsible variant="section">
          <Collapsible.Trigger><Collapsible.Icon />{t("inbox.sourceData")}</Collapsible.Trigger>
          <Collapsible.Panel>
            <div className="mb-2 text-xs text-fg-muted">
              {t("inbox.sourceMetadata")}: {approval.action} · {approval.priority}
            </div>
            <JsonValueView value={approval.payload} />
            <div className="mt-3 space-y-2">
              <DecisionSourceLinks approval={approval} />
              <DecisionTargetLink approval={approval} />
            </div>
          </Collapsible.Panel>
        </Collapsible>
      </div>
    </PageAside>
  );
}

function DecisionTargetLink({ approval }: { approval: PendingWorkflowDecision }): React.ReactElement | null {
  const t = useWorkflowsT();
  const recordHref = useResourceRecordHrefLookup();
  const target = approval.target_reference;
  const base = target ? recordHref(target.model, target.id) : undefined;
  if (!base || !target) return null;
  const href = decisionHref(base, approval.id, target.tab ?? undefined);
  return <TextLink href={href}>{t("inbox.openTarget")}</TextLink>;
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

function FormSpecApprovalResolution({ approval, active, editable, onResolved, reconcile, onDirtyChange, onCommitted, onOpenRecord, onOpenEvidence }: {
  approval: PendingWorkflowDecision; active: boolean; editable: boolean; onResolved: ApprovalTaskProps["onResolved"]; reconcile?: ReconcileApproval; onDirtyChange?: (dirty: boolean) => void;
  onCommitted: (verdict: string) => void;
  onOpenRecord?: WorkflowDecisionContentProps["openRecord"];
  onOpenEvidence?: WorkflowDecisionContentProps["openEvidence"];
}): React.ReactElement {
  const t = useWorkflowsT();
  const { widgets } = useAppRuntime();
  const confirm = useConfirm();
  const compiled = React.useMemo(() => {
    try { return { form: compileDecisionActionFormSpec(approval.decision_schema, widgets), error: null }; }
    catch (cause) { return { form: null, error: errorMessage(cause, "Decision form is unavailable.") }; }
  }, [approval.decision_schema, widgets]);
  const form = compiled.form;
  const contextFields = form?.contextFields ?? [];
  const inputFields = form?.inputFields ?? [];
  const contextValues = React.useMemo(
    () => formSpecInitialValues(contextFields, approval.payload),
    [approval.payload, contextFields],
  );
  const source = active ? approval.payload : approval.resolution;
  const seed = formSpecInitialValues(inputFields, source);
  const authoredAction = source && typeof source === "object" && !Array.isArray(source)
    ? (source as Record<string, unknown>).action : null;
  if (typeof authoredAction === "string" && form?.options.some((option) => option.value === authoredAction)) {
    seed.action = authoredAction;
  }
  const rhf = useForm<Record<string, unknown>>({ defaultValues: seed });
  React.useEffect(() => {
    if (active) return;
    const retained = formSpecInitialValues(inputFields, approval.resolution);
    const resolvedAction = approval.resolution && typeof approval.resolution === "object"
      && !Array.isArray(approval.resolution)
      ? (approval.resolution as Record<string, unknown>).action : null;
    if (typeof resolvedAction === "string" && form?.options.some((option) => option.value === resolvedAction)) {
      retained.action = resolvedAction;
    }
    rhf.reset(retained);
  }, [active, approval.updated_at, approval.resolution, form, inputFields, rhf.reset]);
  const values = (useWatch({ control: rhf.control }) ?? {}) as Record<string, unknown>;
  const selectedAction = typeof values.action === "string" && form?.options.some((option) => option.value === values.action)
    ? values.action : null;
  const branchFields = selectedAction && form ? form.fieldsFor(selectedAction) : [];
  const { errors, isDirty, isSubmitting } = rhf.formState;
  React.useEffect(() => onDirtyChange?.(isDirty), [isDirty, onDirtyChange]);
  const resolution = useApprovalResolver(onResolved, reconcile, onCommitted);
  const resolutionEditable = editable && !resolution.committed;
  const contextCheck = form?.validateContext(approval.payload);
  const submitting = React.useRef(false);
  const contributions = useModelSlot({
    slot: WORKFLOW_DECISION_CONTENT_SLOT,
    model: DECISION_MODEL,
    impl: approval.action,
  });
  // SlotContribution.content is intentionally `unknown` at the framework level
  // (no composer parses a rendered surface's private contract), so this surface
  // owns the WORKFLOW_DECISION_CONTENT_SLOT contract: the function narrowing plus
  // this cast is the trust boundary for a contributed decision-content component.
  const contributedContent = contributions[0]?.content;
  const Content = typeof contributedContent === "function"
    ? contributedContent as React.ComponentType<WorkflowDecisionContentProps>
    : undefined;
  const setValue = React.useCallback((name: string, value: unknown) => {
    if (!branchFields.some((field) => field.name === name)) return;
    rhf.clearErrors(name);
    rhf.setValue(name, value, { shouldDirty: true, shouldValidate: false });
  }, [branchFields, rhf]);
  function applyErrors(messages: Readonly<Record<string, readonly string[]>>): void {
    rhf.clearErrors();
    for (const [path, entries] of Object.entries(messages)) {
      if (entries.length) rhf.setError(path, { type: "decision", message: entries.join(" ") });
    }
  }
  async function submitAction(submitted: Record<string, unknown>): Promise<void> {
    if (submitting.current || !resolutionEditable || resolution.fetching || !contextCheck?.valid) return;
    rhf.clearErrors();
    if (!form || !selectedAction) return;
    const option = form.options.find((entry) => entry.value === selectedAction);
    if (!option) return;
    const candidate = form.project(selectedAction, submitted);
    const check = form.validate(candidate);
    if (!check.valid) { applyErrors(check.messages); return; }
    submitting.current = true;
    try {
      if (option.confirm && !await confirm({
        title: option.label, body: option.confirm, confirm: option.label,
        danger: option.variant === "destructive",
      })) return;
      applyErrors(await resolution.resolve(approval.id, option.verdict, jsonValueFromUnknown(candidate) ?? {}));
    } catch (cause) {
      rhf.setError("root", { type: "mutation", message: errorMessage(cause, t("inbox.actionFailed")) });
    } finally {
      submitting.current = false;
    }
  }
  const messagesFor = (name: string): readonly string[] => fieldErrorMessages(errors[name]);
  const contentProps: WorkflowDecisionContentProps = {
    approval, contextFields, contextValues, inputFields: branchFields, values, setValue,
    messagesFor,
    editable: resolutionEditable, fetching: resolution.fetching, readOnly: !resolutionEditable,
    openRecord: onOpenRecord, openEvidence: onOpenEvidence,
  };
  return (
    <form className="space-y-4" onSubmit={rhf.handleSubmit(submitAction)}>
      {compiled.error ? <ErrorBanner description={compiled.error} /> : null}
      {!contextCheck?.valid ? <ErrorBanner description="Frozen Decision context is unavailable." /> : null}
      {Content ? <Content {...contentProps} /> : <h2 className="text-xl font-semibold text-fg">{approval.step_name || approval.action}</h2>}
      {!Content && contextFields.length ? <section className="space-y-3">
        <h3 className="text-xs font-semibold text-fg-muted">{t("inbox.decisionContext")}</h3>
        {contextFields.map((field) => (
          <LabeledDescriptorField key={field.name} field={field} value={contextValues[field.name]}
            readOnly messages={[]} onChange={() => undefined} />
        ))}
      </section> : null}
      {form ? <section className="space-y-3">
        <h3 className="text-xs font-semibold text-fg-muted">{t("inbox.yourDecision")}</h3>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Decision actions">
          {form.options.map((option) => <Button key={option.value} type="button"
            variant={selectedAction === option.value ? "primary" : "secondary"}
            disabled={!resolutionEditable || resolution.fetching || isSubmitting}
            aria-pressed={selectedAction === option.value}
            onClick={() => { rhf.clearErrors(); rhf.setValue("action", option.value, { shouldDirty: true }); }}>
            {option.label}
          </Button>)}
        </div>
      {branchFields.map((field) => <Controller key={field.name} name={field.name} control={rhf.control}
        render={({ field: controlled }) => <LabeledDescriptorField field={field} value={controlled.value}
          readOnly={field.readOnly || !resolutionEditable || resolution.fetching || isSubmitting} messages={messagesFor(field.name)}
          onChange={(value) => { rhf.clearErrors(field.name); controlled.onChange(value); }} />}
      />)}
      {resolutionEditable && selectedAction ? <div className="flex justify-end"><Button type="submit"
        variant={form.options.find((option) => option.value === selectedAction)?.variant === "destructive" ? "danger" : "primary"}
        loading={resolution.fetching || isSubmitting} disabled={!contextCheck?.valid || isSubmitting}
        >
        {form.options.find((option) => option.value === selectedAction)?.label}
      </Button></div> : null}
      </section>
      : null}
      <PostCommitContinuationBanner resolution={resolution} />
      <ErrorBanner description={errors.root?.message ?? resolution.error?.message} />
    </form>
  );
}

function fieldErrorMessages(value: unknown): readonly string[] {
  if (!value || typeof value !== "object") return [];
  const entries = value as Record<string, unknown>;
  return [...(typeof entries.message === "string" ? [entries.message] : []),
    ...Object.entries(entries).filter(([key]) => key !== "message" && key !== "type")
      .flatMap(([, child]) => fieldErrorMessages(child))];
}

function JsonApprovalResolution({ approval, active, editable, onResolved, reconcile, onDirtyChange, onCommitted }: {
  approval: PendingWorkflowDecision; active: boolean; editable: boolean; onResolved: ApprovalTaskProps["onResolved"]; reconcile?: ReconcileApproval; onDirtyChange?: (dirty: boolean) => void;
  onCommitted: (verdict: string) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const [payload, setPayload] = React.useState<JsonValue>(() => active ? {} : jsonValueFromUnknown(approval.resolution) ?? {});
  const [jsonValid, setJsonValid] = React.useState(true);
  const validationErrors = useDottedPathFieldErrors();
  const [error, setError] = React.useState<string | null>(null);
  const resolution = useApprovalResolver(onResolved, reconcile, onCommitted);
  const resolutionEditable = editable && !resolution.committed;
  const validationError = validationErrors.formSummary;
  async function resolve(verdict: ApprovalVerdict): Promise<void> {
    setError(null); validationErrors.clear();
    if (!jsonValid) return;
    try { validationErrors.replace(await resolution.resolve(approval.id, verdict, payload)); }
    catch (cause) { setError(errorMessage(cause, t("inbox.actionFailed"))); }
  }
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold text-fg">{approval.step_name || approval.action}</h2>
      <FieldRoot invalid={Boolean(error || validationError)}>
        <FieldLabel>{t("inbox.resolution")}</FieldLabel>
        <JsonEditor
          value={payload}
          field={{ label: t("inbox.resolution") }}
          readOnly={!resolutionEditable || resolution.fetching}
          onValidityChange={setJsonValid}
          onChange={(value) => {
            validationErrors.clear();
            onDirtyChange?.(true);
            setPayload(jsonValueFromUnknown(value) ?? {});
          }}
        />
        <FieldDescription>{t("json.label")}</FieldDescription>
      </FieldRoot>
      <PostCommitContinuationBanner resolution={resolution} />
      <ErrorBanner description={error ?? resolution.error?.message ?? validationError} />
      {resolutionEditable ? <ApprovalVerdictButtons disabled={!jsonValid} fetching={resolution.fetching} onResolve={resolve} /> : null}
    </section>
  );
}

function ApprovalVerdictButtons({ disabled = false, fetching, onResolve }: { disabled?: boolean; fetching: boolean; onResolve: (verdict: ApprovalVerdict) => void | Promise<void> }): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="flex flex-wrap justify-end gap-2">
    <Button type="button" variant="ghost" disabled={disabled} loading={fetching} onClick={() => void onResolve("ESCALATE")}><Glyph name="workflow-escalate" />{t("inbox.escalate")}</Button>
    <Button type="button" variant="secondary" disabled={disabled} loading={fetching} onClick={() => void onResolve("REJECT")}><Glyph name="workflow-reject" />{t("inbox.reject")}</Button>
    <Button type="button" variant="primary" disabled={disabled} loading={fetching} onClick={() => void onResolve("COMPLETE")}><Glyph name="workflow-approve" />{t("inbox.complete")}</Button>
  </div>;
}

function useApprovalResolver(onResolved: ApprovalTaskProps["onResolved"], reconcile: ReconcileApproval | undefined, onCommitted: (verdict: string) => void): {
  resolve: (approval: string, verdict: ApprovalVerdict, payload: JsonValue) => Promise<DottedPathFieldErrorMap>;
  retryContinuation: () => Promise<void>;
  committed: boolean; continuationError: Error | null; fetching: boolean; error: Error | null;
} {
  const t = useWorkflowsT();
  const [decide, state] = useAuthoredMutation(DecideWorkflowDecisionDocument, {
    dataProviderName: "public", invalidateModels: [DECISION_MODEL],
    shouldInvalidate: (data) => data?.decide?.validation_errors == null,
  });
  const ambiguous = React.useRef(false);
  const inFlight = React.useRef(false);
  const committedRef = React.useRef(false);
  const [resolving, setResolving] = React.useState(false);
  const [committed, setCommitted] = React.useState(false);
  const [continuationError, setContinuationError] = React.useState<Error | null>(null);
  const retryContinuation = React.useCallback(async () => {
    setResolving(true);
    setContinuationError(null);
    try {
      await onResolved();
    } catch (cause) {
      setContinuationError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setResolving(false);
    }
  }, [onResolved]);
  const resolve = React.useCallback(async (approval: string, verdict: ApprovalVerdict, payload: JsonValue): Promise<DottedPathFieldErrorMap> => {
    if (inFlight.current || committedRef.current) return {};
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
        committedRef.current = true;
        onCommitted(expectedVerdict);
        setCommitted(true);
        await retryContinuation();
      }
      return errors;
    } finally {
      inFlight.current = false;
      setResolving(false);
    }
  }, [decide, onCommitted, reconcile, retryContinuation, t]);
  return {
    resolve, retryContinuation, committed, continuationError,
    fetching: state.fetching || resolving, error: state.error,
  };
}

function PostCommitContinuationBanner({ resolution }: {
  resolution: ReturnType<typeof useApprovalResolver>;
}): React.ReactElement | null {
  const t = useWorkflowsT();
  if (!resolution.committed || !resolution.continuationError) return null;
  return <ErrorBanner
    title={t("inbox.resolutionRecorded")}
    description={t("inbox.queueRefreshFailed")}
    actions={<Button type="button" variant="secondary" loading={resolution.fetching}
      onClick={() => void resolution.retryContinuation()}>{t("inbox.retryQueueRefresh")}</Button>}
  />;
}
