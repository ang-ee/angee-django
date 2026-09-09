import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Button, ErrorBanner, FieldDescriptorControl, MutationDialog, mutationDialogValueCodecs, type MutationDialogControlProps, type MutationDialogField, type MutationDialogValues } from "@angee/ui";

import { WorkflowTestFixtureSourceDocument, WorkflowTestFixtureSourcesDocument, WorkflowTestPlanDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";

const WorkflowTestLaunchContext = React.createContext<((nodeKey: string) => void) | null>(null);

export function WorkflowTestLaunchProvider({ children, onTestStep }: { children: React.ReactNode; onTestStep: (nodeKey: string) => void }): React.ReactElement {
  return <WorkflowTestLaunchContext.Provider value={onTestStep}>{children}</WorkflowTestLaunchContext.Provider>;
}
export function useWorkflowTestLaunch(): ((nodeKey: string) => void) | null { return React.useContext(WorkflowTestLaunchContext); }

type FixtureRole = "OUTPUT" | "MAP_ITEM";
export interface WorkflowTestFixtureValue {
  stepKey: string;
  role: FixtureRole;
  itemIndex?: number;
  mode: "manual" | "captured";
  valuePresent: boolean;
  value?: unknown;
  outcome?: string;
  capturedAttempt?: string;
}
export interface WorkflowTestFixtureInputValue {
  step_key: string;
  role: FixtureRole;
  item_index?: number;
  value?: unknown;
  outcome?: string;
  captured_attempt?: string;
}
export interface WorkflowTestSetupValues extends Record<string, unknown> {
  subjectId?: string;
  inputPresent: boolean;
  input: unknown;
  fixtures: WorkflowTestFixtureValue[];
}
interface FixtureRequirement { role: FixtureRole; step_key: string; item_index_required: boolean; satisfied: boolean }

export function WorkflowTestSetup({ open, onOpenChange, workflowId, revision, sourceStepId, previousRunId, dirty, retrying, locked, retainedValues, subjectDeclaration, onSubmit, onStarted }: {
  open: boolean; onOpenChange: (open: boolean) => void; workflowId: string; revision: number; sourceStepId?: string; previousRunId?: string;
  dirty: boolean; retrying: boolean; locked: boolean; retainedValues?: WorkflowTestSetupValues; subjectDeclaration: string;
  onSubmit: (values: WorkflowTestSetupValues) => Promise<{ ok: boolean; id?: string | null } | undefined>; onStarted: (runId: string) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const [planReady, setPlanReady] = React.useState(false);
  React.useEffect(() => { if (!open) setPlanReady(false); }, [open]);
  const fields = React.useMemo<readonly MutationDialogField[]>(() => [
    ...(subjectDeclaration ? [{ name: "subject", label: t("test.subjectLabel"), description: t("test.subjectDescription", { subject: subjectDeclaration }), required: true, readOnly: locked, relation: { resource: subjectDeclaration } } satisfies MutationDialogField] : []),
    { name: "input", label: t("test.inputLabel"), description: t("test.inputDescription"), widget: "json", nullable: true, omittable: true, readOnly: locked },
    { name: "fixtures", label: t("test.plan"), controlLabelMode: "group", readOnly: locked, control: (control) => <TestPlanControl {...control} workflowId={workflowId} revision={revision} sourceStepId={sourceStepId} previousRunId={previousRunId} subjectDeclaration={subjectDeclaration} onPlanReady={setPlanReady} /> },
  ], [locked, previousRunId, revision, sourceStepId, subjectDeclaration, t, workflowId]);
  return <MutationDialog<WorkflowTestSetupValues, { ok: boolean; id?: string | null } | undefined>
    open={open} onOpenChange={onOpenChange} title={t(sourceStepId ? "test.nodeTitle" : "test.title")} description={t("test.description", { revision })}
    fields={fields} initialValues={retainedValues ? { ...(retainedValues.subjectId ? { subject: retainedValues.subjectId } : {}), ...(retainedValues.inputPresent ? { input: retainedValues.input } : {}), fixtures: retainedValues.fixtures } : { fixtures: [] }}
    submitLabel={t(retrying ? "test.retrySubmit" : dirty ? "test.saveSubmit" : "test.submit")} submittingLabel={t("test.submitting")} errorFallback={t("test.failed")}
    parseValues={(values) => parseWorkflowTestValues(values, Boolean(subjectDeclaration))} onSubmit={onSubmit} closeOnSubmit={false}
    canSubmit={() => planReady}
    onSubmitted={(result) => { if (result?.ok && result.id) { onStarted(result.id); onOpenChange(false); } }} />;
}

function TestPlanControl({ value, readOnly, onChange, dialogValues, workflowId, revision, sourceStepId, previousRunId, subjectDeclaration, onPlanReady }: MutationDialogControlProps & {
  workflowId: string; revision: number; sourceStepId?: string; previousRunId?: string; subjectDeclaration: string; onPlanReady: (ready: boolean) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const fixtures = fixtureValues(value);
  const subjectReady = !subjectDeclaration || typeof dialogValues.subject === "string" && dialogValues.subject.length > 0;
  const query = useAuthoredQuery(WorkflowTestPlanDocument, {
    workflow: workflowId, expectedRevision: revision, scope: sourceStepId ? "NODE" : "WHOLE",
    ...(sourceStepId ? { sourceStep: sourceStepId } : {}), ...(previousRunId ? { previousRun: previousRunId } : {}),
    ...(subjectDeclaration && typeof dialogValues.subject === "string" ? { subject: { subject_declaration: subjectDeclaration, id: dialogValues.subject } } : {}),
    ...(Object.hasOwn(dialogValues, "input") && dialogValues.input !== undefined ? { input: dialogValues.input } : {}), fixtures: fixtures.map(fixtureInput),
  }, { enabled: Boolean(workflowId) && Number.isInteger(revision) && revision >= 0 && subjectReady });
  const plan = query.data?.workflow_test_plan;
  const requirements = (plan?.required_fixtures ?? []) as readonly FixtureRequirement[];
  React.useEffect(() => {
    const next = requirements.reduce<WorkflowTestFixtureValue[]>((items, requirement) => items.some((item) => sameSlot(item, requirement)) ? items : [...items, { stepKey: requirement.step_key, role: requirement.role, mode: "manual", valuePresent: false }], fixtures);
    if (next.length !== fixtures.length) onChange(next);
  }, [fixtures, onChange, requirements]);
  React.useEffect(() => {
    onPlanReady(subjectReady && Boolean(plan) && !query.isFetching && !query.error && (plan?.diagnostics.length ?? 0) === 0
      && requirements.every((requirement) => requirement.satisfied));
  }, [onPlanReady, plan, query.error, query.isFetching, requirements, subjectReady]);
  if (!subjectReady) return <span className="text-sm text-foreground-muted">{t("test.chooseSubjectForPlan")}</span>;
  if (query.error) return <ErrorBanner description={t("test.planError")} />;
  if (!plan) return <span className="text-sm text-foreground-muted">{t("test.loadingPlan")}</span>;
  return <div className="grid gap-4">
    {plan.freshness.length ? <section className="grid gap-1 text-sm"><strong>{t("test.freshness")}</strong>{plan.freshness.map((item, index) => <span key={`${item.code}:${index}`}>{t("test.freshnessReason", { code: item.code, step: item.step_key ?? "", field: item.field })}</span>)}</section> : null}
    {plan.diagnostics.length ? <section className="grid gap-1 text-sm"><strong>{t("test.diagnostics")}</strong>{plan.diagnostics.map((item, index) => { const operation = plan.operations.find((candidate) => candidate.step_id === item.id); const owner = operation?.label || operation?.key || item.kind; const field = item.field ? ` · ${item.field}` : ""; return <span key={`${item.code}:${index}`}><strong>{owner}{field}:</strong> {item.message}</span>; })}</section> : null}
    <section className="grid gap-1 text-sm"><strong>{t("test.effects")}</strong>{plan.operations.map((operation) => <span key={operation.step_id}>{operation.label || operation.key}: {operation.replaced_by_output ? t("test.effectSubstituted") : effectLabel(operation, t)}</span>)}</section>
    {requirements.map((requirement) => { const index = fixtures.findIndex((item) => sameSlot(item, requirement)); const fixture = fixtures[index]; return !fixture ? null : <FixtureControl key={`${requirement.role}:${requirement.step_key}`} workflowId={workflowId} requirement={requirement} outcomes={plan.operations.find((operation) => operation.key === requirement.step_key)?.outcomes ?? []} value={fixture} readOnly={readOnly} onChange={(next) => onChange(fixtures.map((item, current) => current === index ? next : item))} />; })}
  </div>;
}

function FixtureControl({ workflowId, requirement, outcomes, value, readOnly, onChange }: { workflowId: string; requirement: FixtureRequirement; outcomes: readonly { key: string; label: string }[]; value: WorkflowTestFixtureValue; readOnly: boolean; onChange: (value: WorkflowTestFixtureValue) => void }): React.ReactElement {
  const t = useWorkflowsT();
  const [after, setAfter] = React.useState<string | undefined>();
  const [sourceItems, setSourceItems] = React.useState<Array<{ attempt_id: string; step_key: string; outcome: string; recorded_at: string }>>([]);
  const sourceVariables = { workflow: workflowId, role: requirement.role, stepKey: requirement.step_key, ...(value.itemIndex === undefined ? {} : { itemIndex: value.itemIndex }) };
  const sources = useAuthoredQuery(WorkflowTestFixtureSourcesDocument, { ...sourceVariables, ...(after ? { after } : {}), first: 20 }, { enabled: value.mode === "captured" && (!requirement.item_index_required || value.itemIndex !== undefined) });
  const selected = useAuthoredQuery(WorkflowTestFixtureSourceDocument, { ...sourceVariables, attempt: value.capturedAttempt ?? "" }, { enabled: value.mode === "captured" && Boolean(value.capturedAttempt) });
  const captured = selected.data?.workflow_test_fixture_source;
  React.useEffect(() => {
    const page = sources.data?.workflow_test_fixture_sources.items;
    if (!page) return;
    setSourceItems((current) => after ? [...current, ...page.filter((item) => !current.some((existing) => existing.attempt_id === item.attempt_id))] : [...page]);
  }, [after, sources.data?.workflow_test_fixture_sources.items]);
  React.useEffect(() => { setAfter(undefined); setSourceItems([]); }, [requirement.role, requirement.step_key, value.itemIndex]);
  return <fieldset className="grid gap-2 rounded-md border border-border-subtle p-3"><legend className="px-1 text-sm font-medium">{requirement.step_key} · {t(requirement.role === "OUTPUT" ? "test.outputFixture" : "test.mapItemFixture")}</legend>
    {requirement.item_index_required ? <FieldDescriptorControl field={{ name: "item_index", label: t("test.itemIndex"), widget: "integer" }} readOnly={readOnly} value={value.itemIndex} onChange={(next) => onChange({ ...value, itemIndex: typeof next === "number" ? next : undefined, capturedAttempt: undefined })} /> : null}
    <FieldDescriptorControl field={{ name: "mode", label: t("test.fixtureSource"), widget: "select", options: [{ value: "manual", label: t("test.fixtureManual") }, { value: "captured", label: t("test.fixtureCaptured") }] }} readOnly={readOnly} value={value.mode} onChange={(next) => onChange({ ...value, mode: next === "captured" ? "captured" : "manual", capturedAttempt: undefined })} />
    {value.mode === "manual" ? <><Button type="button" size="sm" variant="secondary" disabled={readOnly} onClick={() => onChange({ ...value, valuePresent: !value.valuePresent, value: value.valuePresent ? undefined : null })}>{value.valuePresent ? t("test.omitValue") : t("test.valuePresent")}</Button>{value.valuePresent ? <FieldDescriptorControl field={{ name: "value", label: t("test.fixtureValue"), widget: "json", nullable: true }} readOnly={readOnly} value={value.value} onChange={(next) => onChange({ ...value, value: next })} /> : null}{requirement.role === "OUTPUT" ? <FieldDescriptorControl field={{ name: "outcome", label: t("test.fixtureOutcome"), widget: "select", options: [{ value: "", label: t("test.chooseOutcome") }, ...outcomes.map((outcome) => ({ value: outcome.key, label: outcome.label }))] }} readOnly={readOnly} value={value.outcome ?? ""} onChange={(next) => onChange({ ...value, outcome: typeof next === "string" ? next : "" })} /> : null}</> : <><ErrorBanner description={sources.error || selected.error ? t("test.captureError") : null} />{sources.isFetching && sourceItems.length === 0 ? <span className="text-sm text-foreground-muted">{t("test.loadingCaptures")}</span> : null}{!sources.isFetching && sourceItems.length === 0 ? <span className="text-sm text-foreground-muted">{t("test.noCaptures")}</span> : null}<FieldDescriptorControl field={{ name: "captured_attempt", label: t("test.chooseCapture"), widget: "select", options: [{ value: "", label: t("test.chooseCapture") }, ...sourceItems.map((item) => ({ value: item.attempt_id, label: `${item.step_key} · ${item.outcome} · ${new Date(item.recorded_at).toLocaleString()}` }))] }} readOnly={readOnly} value={value.capturedAttempt ?? ""} onChange={(next) => onChange({ ...value, capturedAttempt: typeof next === "string" && next ? next : undefined })} />{sources.data?.workflow_test_fixture_sources.next_after ? <Button type="button" size="sm" variant="secondary" disabled={sources.isFetching} onClick={() => setAfter(sources.data?.workflow_test_fixture_sources.next_after ?? undefined)}>{t("test.loadMoreCaptures")}</Button> : null}{captured ? <><span className="text-xs text-foreground-muted">{t("test.captureProvenance", { revision: captured.summary.workflow_revision, run: captured.summary.run_id })}</span>{captured.value_present ? <FieldDescriptorControl field={{ name: "captured_value", label: t("test.fixtureValue"), widget: "json" }} readOnly value={captured.value} /> : <span className="text-xs text-foreground-muted">{t("test.capturedAbsent")}</span>}</> : null}</>}
    {!requirement.satisfied ? <span className="text-xs text-danger">{t("test.fixtureRequired")}</span> : null}
  </fieldset>;
}

function fixtureValues(value: unknown): WorkflowTestFixtureValue[] { return Array.isArray(value) ? value as WorkflowTestFixtureValue[] : []; }
function sameSlot(value: WorkflowTestFixtureValue, requirement: FixtureRequirement): boolean { return value.role === requirement.role && value.stepKey === requirement.step_key; }
export function fixtureInput(value: WorkflowTestFixtureValue): WorkflowTestFixtureInputValue { return { step_key: value.stepKey, role: value.role, ...(value.itemIndex === undefined ? {} : { item_index: value.itemIndex }), ...(value.mode === "captured" && value.capturedAttempt ? { captured_attempt: value.capturedAttempt } : value.valuePresent ? { value: value.value } : {}), ...(value.mode === "manual" && value.outcome ? { outcome: value.outcome } : {}) }; }
function effectLabel(operation: { effect: string; effect_description: string }, t: ReturnType<typeof useWorkflowsT>): string { if (operation.effect_description.trim()) return operation.effect_description.trim(); return operation.effect === "NONE" ? t("canvas.effect.none") : operation.effect === "READ" ? t("canvas.effect.read") : operation.effect === "WRITE" ? t("canvas.effect.write") : operation.effect === "EXTERNAL" ? t("canvas.effect.external") : t("test.effectUnknown"); }
export function parseWorkflowTestValues(values: MutationDialogValues, needsSubject: boolean): WorkflowTestSetupValues { return { ...(needsSubject ? { subjectId: mutationDialogValueCodecs.requiredString(values.subject, "subject") } : {}), inputPresent: Object.hasOwn(values, "input") && values.input !== undefined, input: values.input, fixtures: fixtureValues(values.fixtures) }; }
