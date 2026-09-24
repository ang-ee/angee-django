import * as React from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { useAuthoredMutation, type DocumentVariables } from "@angee/refine";
import {
  Badge, Button, Collapsible, ErrorBanner, fieldErrorMessages, isCompositeFieldDescriptor,
  Glyph, JsonValueView, LabeledDescriptorField, LazyBoundary, TextLink, formSpecInitialValues,
  LARGE_VIEWPORT_QUERY,
  PageAside,
  deserializeFormSpec, errorMessage, jsonValueFromUnknown, normalizeFormSpecValues, statusTone, useAppRuntime, useConfirm, useResourceRecordHrefLookup, useRouteHref, validationErrorMap,
  optionalTranslation, useT,
  recordTargetHref, useMediaQuery, useModelSlot,
  useRecordPeek,
  type DottedPathFieldErrorMap, type FormSpecFieldDescriptor, type JsonValue, type RecordPeekOpen, type RecordPeekReference,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import { decisionHref } from "../decision-navigation";
import { compileDecisionActionFormSpec } from "../decision-action-form";
import { DecideWorkflowDecisionDocument, type PendingWorkflowDecision } from "../documents.public";
import { useWorkflowsT } from "../i18n";
import { WORKFLOW_DECISION_CONTENT_SLOT } from "../slots";
import { DECISION_OBJECT_WIDGET } from "./DecisionContextWidgets";

const DECISION_MODEL = "workflows.Decision";
export type ApprovalVerdict = DocumentVariables<typeof DecideWorkflowDecisionDocument>["verdict"];
export type WorkflowDecisionRecordReference = RecordPeekReference;
export interface WorkflowDecisionActionPresentation {
  namespace: string;
  /** Composed namespace key prefix; each action resolves `${keyPrefix}.${action}`. */
  keyPrefix: string;
}
export interface WorkflowDecisionContentProps {
  approval: PendingWorkflowDecision;
  contextFields: readonly FormSpecFieldDescriptor[];
  contextValues: Readonly<Record<string, unknown>>;
  inputFields: readonly FormSpecFieldDescriptor[];
  values: Readonly<Record<string, unknown>>;
  setValue: (name: string, value: unknown) => void;
  messagesFor: (name: string) => readonly string[];
  /** Framework-owned action choice, placed by rich fragments before their inputs. */
  actionPicker?: React.ReactNode;
  /** Select a framework-owned action without routing it through input-field writes. */
  selectAction: (action: string) => void;
  editable: boolean;
  fetching: boolean;
  readOnly: boolean;
  openRecord?: RecordPeekOpen;
  openEvidence?: RecordPeekOpen;
}
export type WorkflowDecisionContentComponent = React.ComponentType<WorkflowDecisionContentProps> & {
  /** Fields deliberately placed by the domain fragment through {@link DecisionField}. */
  renderedInputFields?: readonly string[];
  /** The fragment places the framework-owned action picker in its review layout. */
  placesActionPicker?: boolean;
  /** Translate labels through the addon bundle; absent keys retain the frozen schema label. */
  actionPresentation?: WorkflowDecisionActionPresentation;
};
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

/** Render authored Decision context descriptors without adding form ownership. */
export function DecisionContextFields({ fields, values }: {
  fields: readonly FormSpecFieldDescriptor[];
  values: Readonly<Record<string, unknown>>;
}): React.ReactElement | null {
  const t = useWorkflowsT();
  if (!fields.length) return null;
  return <>
    {fields.map((field) => {
      const sharedLabel = field.label ?? contextFieldLabel(field.widget, t);
      const decisionField = field.widget === "object" ? { ...field, widget: DECISION_OBJECT_WIDGET } : field;
      return <LabeledDescriptorField key={field.name}
        field={sharedLabel ? { ...decisionField, label: sharedLabel } : decisionField} value={values[field.name]}
        readOnly messages={[]} onChange={() => undefined} />
    })}
  </>;
}

function contextFieldLabel(widget: string | undefined, t: ReturnType<typeof useWorkflowsT>): string | undefined {
  if (widget === "facts") return t("inbox.contextFacts");
  if (widget === "differences") return t("inbox.contextDifferences");
  if (widget === "reasons") return t("inbox.contextReasons");
  if (widget === "record") return t("inbox.contextRecord");
  if (widget === "object") return t("inbox.contextDetails");
  return undefined;
}

/** Render one native Decision input through the shared FormSpec field owner. */
export function DecisionField({ name, props, label, widget }: {
  name: string;
  props: WorkflowDecisionContentProps;
  label?: string;
  /** Select a registered presentation while preserving the native field contract. */
  widget?: string;
}): React.ReactElement | null {
  const field = props.inputFields.find((candidate) => candidate.name === name);
  if (!field) return null;
  return <LabeledDescriptorField field={{ ...field, label: label ?? field.label, widget: widget ?? field.widget }} value={props.values[name]}
    dialogValues={{ ...props.values }} readOnly={field.readOnly || props.readOnly || props.fetching}
    messages={props.messagesFor(name)} onChange={(value) => props.setValue(name, value)} />;
}

/** Open a Decision reference in the host peek, with its canonical route as fallback. */
export function DecisionReferenceAction({ label, glyph, open, reference, target }: {
  label: string;
  glyph?: string;
  open?: (reference: RecordPeekReference) => void;
  reference: RecordPeekReference;
  target?: "_blank";
}): React.ReactElement {
  const hrefFor = useResourceRecordHrefLookup();
  const baseHref = hrefFor(reference.model, reference.id);
  const href = baseHref ? recordTargetHref(baseHref, {
    tab: reference.tab,
    search: reference.search,
  }) : undefined;
  const labeled = { ...reference, label: reference.label ?? label };
  if (open) {
    return <Button type="button" size="sm" variant="ghost"
      onClick={() => open(labeled)}>{glyph ? <Glyph decorative name={glyph} /> : null}{label}</Button>;
  }
  if (href) return <TextLink href={href} target={target}>{label}</TextLink>;
  return <span className="text-13 text-fg-muted">{label}</span>;
}

/** Open one frozen Decision reference once on a large review surface. */
export function useInitialDecisionPeek(
  props: WorkflowDecisionContentProps,
  reference: RecordPeekReference | undefined,
  open: WorkflowDecisionContentProps["openEvidence"] | WorkflowDecisionContentProps["openRecord"] = props.openEvidence,
): void {
  const largeViewport = useMediaQuery(LARGE_VIEWPORT_QUERY);
  const openedDecision = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!largeViewport || !open || !reference
        || openedDecision.current === props.approval.id) return;
    openedDecision.current = props.approval.id;
    open(reference, { tabActivation: "initial" });
  }, [largeViewport, open, props.approval.id, reference?.id, reference?.model,
    reference?.page, reference?.search, reference?.tab]);
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
        {approval.decision_schema == null ? (active
          ? <div className="space-y-3">
            <h2 className="text-xl font-semibold text-fg">{approval.step_name || approval.action}</h2>
            <ErrorBanner description={t("inbox.schemaRequired")} />
            <DecisionTargetLink approval={approval} />
          </div>
          : <HistoricalDecisionFallback approval={approval} />
        ) : (
          <LazyBoundary pending={null} fallback={active
            ? <ErrorBanner description={t("inbox.invalidFormSpec")} />
            : <HistoricalDecisionFallback approval={approval} />} resetKey={approval.id}>
            {active
              ? <FormSpecApprovalResolution key={approval.id} approval={approval} editable={editable} onResolved={onResolved} reconcile={reconcile} onDirtyChange={onDirtyChange} onCommitted={setCommittedVerdict} onOpenRecord={onOpenRecord ?? openRecord} onOpenEvidence={onOpenEvidence ?? openRecord} />
              : <HistoricalDecisionPresentation key={approval.id} approval={approval}
                onOpenRecord={onOpenRecord ?? openRecord} onOpenEvidence={onOpenEvidence ?? openRecord} />}
          </LazyBoundary>
        )}
        <Collapsible variant="section">
          <Collapsible.Trigger><Collapsible.Icon />{t("inbox.sourceData")}</Collapsible.Trigger>
          <Collapsible.Panel>
            <div className="mb-2 text-xs text-fg-muted">
              {t("inbox.sourceMetadata")}: {approval.action} · {approval.priority}
            </div>
            <JsonValueView value={approval.payload} />
            {!active && approval.resolution ? <div className="mt-3 space-y-2">
              <p className="text-xs font-medium text-fg-muted">{t("inbox.resolution")}</p>
              <JsonValueView value={approval.resolution} />
            </div> : null}
            <div className="mt-3 space-y-2">
              <DecisionSourceLinks approval={approval} />
              {active && approval.decision_schema == null ? null : <DecisionTargetLink approval={approval} />}
            </div>
          </Collapsible.Panel>
        </Collapsible>
      </div>
    </PageAside>
  );
}

function HistoricalDecisionFallback({ approval }: {
  approval: PendingWorkflowDecision;
}): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="space-y-2">
    <h2 className="text-xl font-semibold text-fg">{approval.step_name || approval.action}</h2>
    <p className="text-sm text-fg-muted">{t("inbox.historicalDetailsUnavailable")}</p>
  </div>;
}

function HistoricalDecisionPresentation({ approval, onOpenRecord, onOpenEvidence }: {
  approval: PendingWorkflowDecision;
  onOpenRecord?: WorkflowDecisionContentProps["openRecord"];
  onOpenEvidence?: WorkflowDecisionContentProps["openEvidence"];
}): React.ReactElement {
  const { widgets } = useAppRuntime();
  const fields = React.useMemo(
    () => deserializeFormSpec(approval.decision_schema, widgets),
    [approval.decision_schema, widgets],
  );
  const contextFields = fields.filter((field) => field.layout === "context");
  const inputFields = fields.filter(
    (field) => field.name !== "action" && field.layout !== "context",
  );
  const retainedInputFields = fields.filter((field) => field.layout !== "context");
  const contextValues = React.useMemo(
    () => normalizeFormSpecValues(contextFields, retainedObject(approval.payload)),
    [approval.payload, contextFields],
  );
  const values = React.useMemo(
    () => normalizeFormSpecValues(retainedInputFields, retainedObject(approval.resolution)),
    [approval.resolution, retainedInputFields],
  );
  const Content = useDecisionContent(approval.action);
  const props: WorkflowDecisionContentProps = {
    approval,
    contextFields,
    contextValues,
    inputFields,
    values,
    setValue: () => undefined,
    selectAction: () => undefined,
    messagesFor: () => [],
    actionPicker: null,
    editable: false,
    fetching: false,
    readOnly: true,
    openRecord: onOpenRecord,
    openEvidence: onOpenEvidence,
  };
  const plain = <div className="space-y-4">
    <h2 className="text-xl font-semibold text-fg">{approval.step_name || approval.action}</h2>
    <DecisionContextFields fields={contextFields} values={contextValues} />
    <DecisionContextFields fields={inputFields} values={values} />
  </div>;
  return Content
    ? <LazyBoundary pending={null} fallback={plain} resetKey={approval.id}>
        <Content {...props} />
      </LazyBoundary>
    : plain;
}

function retainedObject(value: unknown): Readonly<Record<string, unknown>> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Readonly<Record<string, unknown>>
    : {};
}

function useDecisionContent(action: string): WorkflowDecisionContentComponent | undefined {
  const contributions = useModelSlot({
    slot: WORKFLOW_DECISION_CONTENT_SLOT,
    model: DECISION_MODEL,
    impl: action,
  });
  // SlotContribution.content is intentionally `unknown` at the framework level
  // (no composer parses a rendered surface's private contract), so this surface
  // owns the WORKFLOW_DECISION_CONTENT_SLOT contract: the function narrowing plus
  // this cast is the trust boundary for a contributed decision-content component.
  const content = contributions[0]?.content;
  return typeof content === "function"
    ? content as WorkflowDecisionContentComponent
    : undefined;
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

function FormSpecApprovalResolution({ approval, editable, onResolved, reconcile, onDirtyChange, onCommitted, onOpenRecord, onOpenEvidence }: {
  approval: PendingWorkflowDecision; editable: boolean; onResolved: ApprovalTaskProps["onResolved"]; reconcile?: ReconcileApproval; onDirtyChange?: (dirty: boolean) => void;
  onCommitted: (verdict: string) => void;
  onOpenRecord?: WorkflowDecisionContentProps["openRecord"];
  onOpenEvidence?: WorkflowDecisionContentProps["openEvidence"];
}): React.ReactElement {
  const t = useWorkflowsT();
  const { i18n, widgets } = useAppRuntime();
  const locale = i18n?.language ?? "en";
  const confirm = useConfirm();
  const compiled = React.useMemo(() => {
    try { return { form: compileDecisionActionFormSpec(approval.decision_schema, widgets, t, locale), error: null }; }
    catch (cause) { return { form: null, error: errorMessage(cause, t("inbox.decisionFormUnavailable")) }; }
  }, [approval.decision_schema, locale, t, widgets]);
  const form = compiled.form;
  const contextFields = form?.contextFields ?? [];
  const inputFields = form?.inputFields ?? [];
  const contextValues = React.useMemo(
    () => formSpecInitialValues(contextFields, approval.payload),
    [approval.payload, contextFields],
  );
  const source = approval.payload;
  const seed = formSpecInitialValues(inputFields, source);
  const authoredAction = source && typeof source === "object" && !Array.isArray(source)
    ? (source as Record<string, unknown>).action : null;
  if (typeof authoredAction === "string" && form?.options.some((option) => option.value === authoredAction)) {
    seed.action = authoredAction;
  }
  const rhf = useForm<Record<string, unknown>>({ defaultValues: seed });
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
  const Content = useDecisionContent(approval.action);
  const actionPresentation = Content?.actionPresentation;
  const actionT = useT(actionPresentation?.namespace ?? "workflows");
  const presentedOptions = React.useMemo(() => form?.options.map((option) => ({
    ...option,
    label: actionPresentation
      ? optionalTranslation(actionT, `${actionPresentation.keyPrefix}.${option.value}`) ?? option.label
      : option.label,
  })) ?? [], [actionPresentation, actionT, form]);
  const renderedInputFields = new Set(Content?.renderedInputFields ?? []);
  const unclaimedBranchFields = branchFields.filter((field) => !renderedInputFields.has(field.name));
  const unsupportedBranchFields = unclaimedBranchFields.filter(isOpaqueDecisionInput);
  const defaultBranchFields = unclaimedBranchFields.filter((field) => !isOpaqueDecisionInput(field));
  const setValue = React.useCallback((name: string, value: unknown) => {
    if (!inputFields.some((field) => field.name === name)) return;
    rhf.clearErrors(name);
    rhf.setValue(name, value, { shouldDirty: true, shouldValidate: false });
  }, [inputFields, rhf]);
  function applyErrors(messages: Readonly<Record<string, readonly string[]>>): void {
    rhf.clearErrors();
    for (const [path, entries] of Object.entries(messages)) {
      if (entries.length) rhf.setError(path, { type: "decision", message: entries.join(" ") });
    }
  }
  async function submitAction(submitted: Record<string, unknown>): Promise<void> {
    if (submitting.current || !resolutionEditable || resolution.fetching
        || !contextCheck?.valid || unsupportedBranchFields.length) return;
    rhf.clearErrors();
    if (!form || !selectedAction) return;
    const option = presentedOptions.find((entry) => entry.value === selectedAction);
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
  const messagesFor = (name: string): readonly string[] => {
    const field = branchFields.find((candidate) => candidate.name === name);
    const composite = field !== undefined && isCompositeFieldDescriptor(field);
    return fieldErrorMessages([errors[name]], composite ? name : undefined);
  };
  const selectAction = React.useCallback((action: string) => {
    if (!form?.options.some((option) => option.value === action)) return;
    rhf.clearErrors();
    rhf.setValue("action", action, { shouldDirty: true });
  }, [form, rhf]);
  const actionPicker = form ? <section className="space-y-3">
    <h3 className="text-xs font-semibold text-fg-muted">{t("inbox.yourDecision")}</h3>
    <div className="flex flex-wrap gap-2" role="group" aria-label={t("inbox.decisionActions")}>
      {presentedOptions.map((option) => <Button key={option.value} type="button"
        variant={selectedAction === option.value ? "primary" : "secondary"}
        disabled={!resolutionEditable || resolution.fetching || isSubmitting}
        aria-pressed={selectedAction === option.value}
        onClick={() => selectAction(option.value)}>
        {option.label}
      </Button>)}
    </div>
  </section> : null;
  const contentProps: WorkflowDecisionContentProps = {
    approval, contextFields, contextValues, inputFields: branchFields, values, setValue,
    messagesFor, actionPicker, selectAction,
    editable: resolutionEditable, fetching: resolution.fetching, readOnly: !resolutionEditable,
    openRecord: onOpenRecord, openEvidence: onOpenEvidence,
  };
  return (
    <form className="space-y-4" onSubmit={rhf.handleSubmit(submitAction)}>
      {compiled.error ? <ErrorBanner description={compiled.error} /> : null}
      {form && !contextCheck?.valid ? <ErrorBanner description={t("inbox.frozenContextUnavailable")} /> : null}
      {!Content ? <h2 className="text-xl font-semibold text-fg">{approval.step_name || approval.action}</h2> : null}
      {!Content && contextFields.length ? <section className="space-y-3">
        <h3 className="text-xs font-semibold text-fg-muted">{t("inbox.decisionContext")}</h3>
        <DecisionContextFields fields={contextFields} values={contextValues} />
      </section> : null}
      {!Content || Content.placesActionPicker ? null : actionPicker}
      {Content && form && contextCheck?.valid ? <Content {...contentProps} /> : null}
      {!Content ? actionPicker : null}
      {form ? <section className="space-y-3">
      {defaultBranchFields.map((field) => <Controller key={field.name} name={field.name} control={rhf.control}
        render={({ field: controlled }) => <LabeledDescriptorField field={field} value={controlled.value}
          readOnly={field.readOnly || !resolutionEditable || resolution.fetching || isSubmitting} messages={messagesFor(field.name)}
          onChange={(value) => { rhf.clearErrors(field.name); controlled.onChange(value); }} />}
      />)}
      {unsupportedBranchFields.length ? <div className="rounded-6 border border-warning-soft bg-warning-soft p-3 text-13 text-fg-2">
        <p className="font-medium">{t("inbox.structuredInputsUnavailable")}</p>
        <p className="mt-1 text-fg-muted">{t("inbox.structuredInputsUnavailableDescription", {
          fields: unsupportedBranchFields.map((field) => field.label ?? field.name).join(", "),
        })}</p>
      </div> : null}
      {resolutionEditable && selectedAction ? <div className="flex justify-end"><Button type="submit"
        variant={presentedOptions.find((option) => option.value === selectedAction)?.variant === "destructive" ? "danger" : "primary"}
        loading={resolution.fetching || isSubmitting}
        disabled={!contextCheck?.valid || isSubmitting || unsupportedBranchFields.length > 0}
        >
        {presentedOptions.find((option) => option.value === selectedAction)?.label}
      </Button></div> : null}
      </section>
      : null}
      <PostCommitContinuationBanner resolution={resolution} />
      <ErrorBanner description={errors.root?.message ?? resolution.error?.message} />
    </form>
  );
}

function isOpaqueDecisionInput(field: FormSpecFieldDescriptor): boolean {
  return (field.kind === "object" || field.kind === "array" || field.kind === "any")
    && !isCompositeFieldDescriptor(field);
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
