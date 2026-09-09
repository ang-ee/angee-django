import * as React from "react";
import { modelLabelSegment, useModelMetadata, useSchemaFieldMetadata } from "@angee/metadata";
import { extractActionOutcome, ResourceContext, useAuthoredMutation, useAuthoredQuery, type ActionOutcome } from "@angee/refine";
import {
  Button,
  ErrorBanner,
  errorMessage,
  Field,
  Form,
  formLevelMessage,
  Group,
  LoadingPanel,
  Statusline,
  StatusSegment,
  TextLink,
  acknowledgeFormSubmit,
  registerForm,
  type FormSubmit,
  type FormViewAcknowledgedSource,
  type RecordToolbarContext,
  type RegisteredFormProps,
  useRouteHref,
} from "@angee/ui";
import { fieldsWithMetadataDefaults } from "@angee/ui/views/model-metadata-defaults";
import { useNavigate, useSearch } from "@tanstack/react-router";

import {
  PublishWorkflowDefinitionDocument,
  SaveWorkflowDefinitionDocument,
  TestWorkflowDefinitionDocument,
  WorkflowDefinitionDocument,
  WorkflowTestRepairContextDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import {
  definitionEdit,
  definitionValueEqual,
  definitionValues,
  type WorkflowDefinitionValues,
} from "./workflow-definition-state";
import { DefinitionHistoryProvider, useDefinitionHistory } from "./workflow-definition-history";
import { inputPreviewRequest, WorkflowInputPreviewProvider } from "./workflow-input-preview";
import { fixtureInput, WorkflowTestLaunchProvider, WorkflowTestSetup, type WorkflowTestSetupValues } from "./WorkflowTestSetup";
import { WorkflowVersionReview } from "./WorkflowVersionReview";

export const WORKFLOW_MODEL = "workflows.Workflow";

export function WorkflowDefinitionForm(props: RegisteredFormProps): React.ReactElement {
  return props.id == null ? <WorkflowCreateForm {...props} /> : <WorkflowDefinitionEditForm key={String(props.id)} {...props} />;
}

function WorkflowCreateForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  return <Form {...props} resource={WORKFLOW_MODEL}><Field name="name" title /></Form>;
}

function WorkflowDefinitionEditForm({ resource: _resource, id, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useWorkflowsT();
  const metadata = useSchemaFieldMetadata();
  const reviewLabels = useDefinitionReviewLabels();
  const { resources: registeredResources } = React.useContext(ResourceContext);
  const subjectOptions = React.useMemo(() => {
    const resources = metadata.resources.filter((resource) => resource.roots.detail && resource.recordRepresentation);
    const labelsByModel = new Map(registeredResources.flatMap((resource) => {
      const modelLabel = resource.meta?.modelLabel;
      const label = resource.meta?.label;
      return typeof modelLabel === "string" && typeof label === "string" ? [[modelLabel, label] as const] : [];
    }));
    const labels = resources.map((resource) => labelsByModel.get(resource.modelLabel) ?? modelLabelSegment(resource.modelLabel));
    const counts = new Map<string, number>();
    for (const label of labels) counts.set(label, (counts.get(label) ?? 0) + 1);
    return resources
      .map((resource, index) => {
        const label = labels[index] ?? resource.modelLabel;
        return {
          value: resource.modelLabel.toLowerCase(),
          label: (counts.get(label) ?? 0) > 1 ? `${label} · ${resource.appLabel}` : label,
        };
      })
      .sort((left, right) => left.label.localeCompare(right.label));
  }, [metadata.resources, registeredResources]);
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as Readonly<Record<string, unknown>>;
  const repairAttempt = typeof search.repairAttempt === "string" ? search.repairAttempt : "";
  const routeHref = useRouteHref();
  const definition = useAuthoredQuery(
    WorkflowDefinitionDocument,
    { workflow: id ?? "" },
    { models: [WORKFLOW_MODEL, "workflows.Step", "workflows.Edge"] },
  );
  const repair = useAuthoredQuery(
    WorkflowTestRepairContextDocument,
    { sourceAttempt: repairAttempt },
    { enabled: Boolean(repairAttempt), models: [WORKFLOW_MODEL, "workflows.Step", "workflows.WorkflowRun", "workflows.StepAttempt"] },
  );
  const [saveDefinition] = useAuthoredMutation(SaveWorkflowDefinitionDocument, {
    invalidateModels: [WORKFLOW_MODEL, "workflows.Step", "workflows.Edge"],
  });
  const [publishDefinition, publishState] = useAuthoredMutation(PublishWorkflowDefinitionDocument, {
    invalidateModels: [WORKFLOW_MODEL],
  });
  const [testDefinition] = useAuthoredMutation(TestWorkflowDefinitionDocument, {
    invalidateModels: ["workflows.WorkflowRun"],
  });
  const [testOpen, setTestOpen] = React.useState(false);
  const [testDirty, setTestDirty] = React.useState(false);
  const [testDefinitionValues, setTestDefinitionValues] = React.useState<WorkflowDefinitionValues | null>(null);
  const [testSelectedNodeKey, setTestSelectedNodeKey] = React.useState<string | null>(null);
  const [startedTest, setStartedTest] = React.useState<{ id: string; revision: number } | null>(null);
  const [testRequestAmbiguous, setTestRequestAmbiguous] = React.useState(false);
  const appliedRepairAttempt = React.useRef<string | null>(null);
  const pendingTest = React.useRef<{
    values: WorkflowTestSetupValues;
    requestKey: string;
    subjectDeclaration: string;
    repairSourceAttempt?: string;
    selectedNodeKey: string | null;
    selectedStepId?: string;
    revision?: number;
    resolve: (value: ActionOutcome | undefined) => void;
    reject: (reason: unknown) => void;
  } | null>(null);
  const [stale, setStale] = React.useState(false);
  const [staleReview, setStaleReview] = React.useState<WorkflowDefinitionValues | null>(null);
  const [reviewOpen, setReviewOpen] = React.useState(false);
  const [reviewCandidate, setReviewCandidate] = React.useState<{
    record: Record<string, unknown>;
    values: WorkflowDefinitionValues;
  } | null>(null);
  const [reloadError, setReloadError] = React.useState<string | null>(null);
  const [formGeneration, setFormGeneration] = React.useState(0);
  const formSurface = React.useRef<RecordToolbarContext["form"] | null>(null);
  const snapshot = definition.data?.workflow_definition ?? null;
  const projected = React.useMemo(() => snapshot ? definitionValues(snapshot) : null, [snapshot]);
  const [acknowledged, setAcknowledged] = React.useState<{
    record: Record<string, unknown>;
    values: WorkflowDefinitionValues;
  } | null>(null);
  React.useEffect(() => {
    if (!snapshot || !projected || snapshot.workflow.id !== id) return;
    setAcknowledged((current) => {
      if (current && current.record.id === snapshot.workflow.id) {
        const record = {
          ...current.record,
          current_published_version: snapshot.workflow.current_published_version,
          publication_status: snapshot.workflow.publication_status,
        };
        if (record.current_published_version === current.record.current_published_version && record.publication_status === current.record.publication_status) return current;
        return { record, values: current.values };
      }
      return { record: snapshot.workflow, values: projected };
    });
  }, [id, projected, snapshot]);
  const values = acknowledged?.values ?? null;
  React.useEffect(() => {
    const context = repair.data?.workflow_test_repair_context;
    if (
      !context
      || !values
      || context.draft_workflow_id !== id
      || !context.current_source_step_id
      || appliedRepairAttempt.current === repairAttempt
    ) return;
    appliedRepairAttempt.current = repairAttempt;
    setTestSelectedNodeKey(context.current_source_step_id);
    setTestDefinitionValues(values);
    setTestDirty(Boolean(formSurface.current?.formIsDirty));
    setTestOpen(true);
  }, [id, repair.data?.workflow_test_repair_context, repairAttempt, values]);
  const repairValues = React.useMemo<WorkflowTestSetupValues | undefined>(() => {
    const context = repair.data?.workflow_test_repair_context;
    if (!context) return undefined;
    return {
      ...(context.subject?.id ? { subjectId: context.subject.id } : {}),
      inputPresent: context.input_present,
      input: context.input,
      fixtures: context.fixtures.map((fixture) => ({
        stepKey: fixture.step_key,
        role: fixture.role,
        ...(fixture.item_index == null ? {} : { itemIndex: fixture.item_index }),
        mode: "captured" as const,
        valuePresent: false,
        capturedAttempt: fixture.attempt_id,
      })),
    };
  }, [repair.data?.workflow_test_repair_context]);
  const source = React.useMemo<FormViewAcknowledgedSource>(() => ({
    record: acknowledged?.record ?? null,
    values,
    loading: definition.isFetching,
    reload: () => {
      void definition.refetch();
    },
  }), [acknowledged?.record, definition.isFetching, definition.refetch, values]);
  const readOnly = props.readOnly || (acknowledged !== null && String(acknowledged.record.status) !== "DRAFT");
  const history = useDefinitionHistory(formSurface, Boolean(readOnly));
  const inputPreview = React.useMemo(() => ({
    prepare: (targetIdentity: string) => {
      const current = formSurface.current?.form.getValues() as WorkflowDefinitionValues | undefined;
      return id && acknowledged?.values && current
        ? inputPreviewRequest(id, acknowledged.values, current, targetIdentity)
        : null;
    },
    stale: () => {
      const current = formSurface.current?.form.getValues() as WorkflowDefinitionValues | undefined;
      if (current) setStaleReview(current);
      setStale(true);
      setReviewOpen(false);
      setReviewCandidate(null);
    },
  }), [acknowledged?.values, id]);

  const launchTest = React.useCallback(async (
    request: NonNullable<typeof pendingTest.current>,
  ): Promise<ActionOutcome | undefined> => {
    if (!id) return undefined;
    return extractActionOutcome(await testDefinition({
      workflow: id,
      expectedRevision: request.revision ?? 0,
      requestKey: request.requestKey,
      ...(request.values.subjectId && request.subjectDeclaration ? {
        subject: { subject_declaration: request.subjectDeclaration, id: request.values.subjectId },
      } : {}),
      ...(request.values.inputPresent ? { input: request.values.input } : {}),
      fixtures: request.values.fixtures.map(fixtureInput),
      ...(request.repairSourceAttempt ? { repairSourceAttempt: request.repairSourceAttempt } : {}),
      ...(request.selectedStepId
        ? { scope: "NODE", sourceStep: request.selectedStepId }
        : { scope: "WHOLE" }),
    }), "start_workflow_test") ?? undefined;
  }, [id, testDefinition]);

  const deliverTest = React.useCallback(async (
    request: NonNullable<typeof pendingTest.current>,
  ): Promise<ActionOutcome | undefined> => {
    try {
      if (request.selectedNodeKey && !request.selectedStepId) throw new Error(t("test.failed"));
      const outcome = await launchTest(request);
      if (!outcome || (outcome.ok && !outcome.id)) throw new Error(t("test.failed"));
      if (!outcome.ok && pendingTest.current === request) pendingTest.current = null;
      setTestRequestAmbiguous(false);
      if (!outcome.ok) {
        throw new Error(formLevelMessage(outcome, new Set()) ?? outcome.message);
      }
      return outcome;
    } catch (error) {
      if (pendingTest.current === request) setTestRequestAmbiguous(true);
      throw error;
    }
  }, [launchTest, t]);

  const submit = React.useCallback<FormSubmit>(async (_data, context) => {
    if (!id || acknowledged?.record.id !== id) return null;
    const baseline = context.baselineValues as WorkflowDefinitionValues;
    const submitted = context.values as WorkflowDefinitionValues;
    const result = await saveDefinition({
      workflow: id,
      expectedRevision: baseline.definition.revision,
      edit: definitionEdit(baseline, submitted),
    });
    const payload = result?.save_workflow_definition;
    if (!payload || payload.status !== "SUCCESS" || payload.revision == null) {
      if (payload?.status === "STALE") {
        setStale(true);
        setStaleReview(submitted);
        setReviewOpen(false);
        setReviewCandidate(null);
      }
      const error = definitionSubmitError(payload, submitted, t);
      const pending = pendingTest.current;
      if (pending && pending.revision === undefined) {
        pending.reject(error);
        if (pendingTest.current === pending) pendingTest.current = null;
      }
      throw error;
    }
    const accepted = acceptedDefinition(submitted, payload);
    history.rebase(baseline, submitted, payload);
    const { definition: _definition, ...acceptedWorkflow } = accepted;
    const record = { ...(acknowledged?.record ?? {}), ...acceptedWorkflow };
    setAcknowledged({ record, values: accepted });
    setStale(false);
    setStaleReview(null);
    setReviewOpen(false);
    setReviewCandidate(null);
    if (pendingTest.current && pendingTest.current.revision === undefined) {
      pendingTest.current.revision = payload.revision;
      if (pendingTest.current.selectedNodeKey) {
        pendingTest.current.selectedStepId = accepted.definition.nodes[
          pendingTest.current.selectedNodeKey
        ]?.id;
      }
    }
    return acknowledgeFormSubmit(
      record,
      accepted,
      reconcileDefinition,
    );
  }, [acknowledged?.record, history, id, saveDefinition, t]);

  const requestTest = React.useCallback((setup: WorkflowTestSetupValues) => {
    const surface = formSurface.current;
    return new Promise<ActionOutcome | undefined>((resolve, reject) => {
      const retained = pendingTest.current;
      const request = retained ?? {
        values: setup,
        requestKey: newTestRequestKey(),
        subjectDeclaration: testDefinitionValues?.subject_declaration ?? "",
        selectedNodeKey: testSelectedNodeKey,
        repairSourceAttempt: repairAttempt || undefined,
        selectedStepId: testSelectedNodeKey
          ? repairAttempt
            ? repair.data?.workflow_test_repair_context?.current_source_step_id ?? undefined
            : testDefinitionValues?.definition.nodes[testSelectedNodeKey]?.id
              ?? values?.definition.nodes[testSelectedNodeKey]?.id
          : undefined,
        resolve,
        reject,
      };
      request.resolve = resolve;
      request.reject = reject;
      pendingTest.current = request;
      if (retained?.revision !== undefined) {
        void deliverTest(retained).then(resolve, reject);
        return;
      }
      if (!surface?.formIsDirty) {
        request.revision ??= testDefinitionValues?.definition.revision ?? values?.definition.revision ?? 0;
        void deliverTest(request).then(resolve, reject);
        return;
      }
      void surface.submitForm().then(() => {
        if (pendingTest.current !== request) return;
        if (!request.revision) {
          pendingTest.current = null;
          reject(new Error(t("test.failed")));
        } else void deliverTest(request).then(resolve, reject);
      }, (error) => {
        if (pendingTest.current === request && request.revision === undefined) {
          pendingTest.current = null;
          setTestRequestAmbiguous(false);
        }
        reject(error);
      });
    });
  }, [deliverTest, repair.data?.workflow_test_repair_context?.current_source_step_id, repairAttempt, t, testDefinitionValues, testSelectedNodeKey, values]);

  const reviewLatest = React.useCallback(async () => {
    setReloadError(null);
    const current = formSurface.current?.form.getValues() as WorkflowDefinitionValues | undefined;
    if (current) setStaleReview(current);
    try {
      const refreshed = await definition.refetch();
      const next = refreshed.data?.workflow_definition;
      if (!next || next.workflow.id !== id) throw new Error(t("form.latestUnavailable"));
      setReviewCandidate({ record: next.workflow, values: definitionValues(next) });
      setReviewOpen(true);
    } catch (error) {
      setReloadError(errorMessage(error, t("form.latestUnavailable")));
    }
  }, [definition.refetch, id, t]);

  const discardAndReload = React.useCallback(() => {
    if (!reviewCandidate) return;
    history.reset();
    setAcknowledged(reviewCandidate);
    setStale(false);
    setStaleReview(null);
    setReviewOpen(false);
    setReviewCandidate(null);
    setReloadError(null);
    setFormGeneration((generation) => generation + 1);
  }, [history, reviewCandidate]);

  const publish = React.useCallback(async (context: RecordToolbarContext) => {
    if (!id || acknowledged?.record.id !== id) return;
    const current = context.form.form.getValues() as WorkflowDefinitionValues;
    try {
      const result = await publishDefinition({
        workflow: id,
        expectedRevision: current.definition.revision,
      });
      const payload = result?.publish_workflow_definition;
      if (!payload || payload.status !== "SUCCESS") throw new Error(definitionFailure(payload, t));
      context.reload();
    } catch (error) {
      context.form.form.setError("root.server", { type: "server", message: errorMessage(error, t("form.definitionSaveFailed")) });
    }
  }, [acknowledged?.record, id, publishDefinition, t]);

  const openTest = React.useCallback((nodeKey: string | null) => {
    if (!pendingTest.current) {
      setTestDirty(Boolean(formSurface.current?.formIsDirty));
      setTestDefinitionValues(
        (formSurface.current?.form.getValues() as WorkflowDefinitionValues | undefined) ?? values,
      );
      setTestSelectedNodeKey(nodeKey);
      setTestRequestAmbiguous(false);
    }
    setTestOpen(true);
  }, [values]);

  if (!acknowledged) {
    return definition.error
      ? <ErrorBanner description={errorMessage(definition.error, t("form.definitionUnavailable"))} />
      : <LoadingPanel message={t("canvas.loading")} />;
  }

  return (<>
    {stale ? <div className="grid gap-2"><ErrorBanner description={t("form.staleDefinition")} /><ErrorBanner description={!reviewOpen ? reloadError : null} /><Button type="button" size="sm" variant="secondary" onClick={() => { void reviewLatest(); }}>{t("form.reviewDefinition")}</Button></div> : null}
    {reviewOpen && staleReview ? <section className="grid gap-2 border-b border-border-subtle p-4" aria-label={t("form.staleReview")}>
      <strong>{t("form.staleReview")}</strong>
      <div className="grid gap-2 text-sm">{definitionChanges(reviewCandidate?.values ?? null, staleReview, reviewLabels, t).map((change) => <div key={change.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2"><strong>{change.label}</strong><span><small>{t("form.yourEdits")}</small><br />{displayValue(change.local, t)}</span><span><small>{t("form.latestSaved")}</small><br />{displayValue(change.remote, t)}</span></div>)}</div>
      <ErrorBanner description={reloadError} />
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => setReviewOpen(false)}>{t("form.cancelReview")}</Button><Button type="button" size="sm" variant="danger" disabled={!reviewCandidate} onClick={discardAndReload}>{t("form.discardReload")}</Button></div>
    </section> : null}
    {startedTest ? <Statusline><StatusSegment><span>{t(repairAttempt ? "test.repairStarted" : "test.started", { revision: startedTest.revision })} <TextLink href={routeHref("workflows.run", { id: startedTest.id })} target="_blank" rel="noreferrer">{t("test.openRun")}</TextLink></span></StatusSegment></Statusline> : null}
    <WorkflowTestLaunchProvider onTestStep={(nodeKey) => openTest(nodeKey)}><DefinitionHistoryProvider value={history}><WorkflowInputPreviewProvider value={inputPreview}><Form
      key={formGeneration}
      {...props}
      resource={WORKFLOW_MODEL}
      id={id}
      readOnly={readOnly}
      acknowledgedSource={source}
      submit={submit}
      onFieldInteractionStart={history.start}
      onFieldInteractionCommit={history.commit}
      onDiscarded={history.reset}
      toolbarStart={(context) => {
        formSurface.current = context.form;
        return readOnly ? <div className="flex items-center gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => {
          if (values?.lineage_id) void navigate({ to: routeHref("workflows.workflow", { id: values.lineage_id }) });
        }}>{t("form.openDraft")}</Button>{id && values?.lineage_id && Number(values.version) > 0 ? <WorkflowVersionReview
          draftId={String(values.lineage_id)} sourceId={id} sourceVersion={Number(values.version)}
          onRestored={(draftId) => { void navigate({ to: routeHref("workflows.workflow", { id: draftId }) }); }}
        /> : null}</div> : <DefinitionActions context={context} history={history} publishState={publishState} sourceLoading={Boolean(source.loading)} issues={values?.definition.readiness.length ?? 0} onPublish={publish} onTest={() => openTest(null)} />;
      }}
    >
      {workflowFields(t, subjectOptions)}
      {props.children}
    </Form>{values?.definition.readiness.length ? <Statusline><StatusSegment><span role="status" aria-live="polite">{t(values.definition.readiness.length === 1 ? "form.publishBlockedOne" : "form.publishBlockedMany", { count: values.definition.readiness.length })}</span></StatusSegment></Statusline> : null}</WorkflowInputPreviewProvider></DefinitionHistoryProvider></WorkflowTestLaunchProvider>
    <WorkflowTestSetup
      open={testOpen}
      onOpenChange={(open) => {
        setTestOpen(open);
      }}
      workflowId={id ?? ""}
      revision={testRequestAmbiguous ? pendingTest.current?.revision ?? 0 : testDefinitionValues?.definition.revision ?? values?.definition.revision ?? 0}
      sourceStepId={testRequestAmbiguous ? pendingTest.current?.selectedStepId : repair.data?.workflow_test_repair_context?.current_source_step_id ?? (testSelectedNodeKey ? testDefinitionValues?.definition.nodes[testSelectedNodeKey]?.id ?? values?.definition.nodes[testSelectedNodeKey]?.id : undefined)}
      previousRunId={repair.data?.workflow_test_repair_context?.source_run_id}
      dirty={testRequestAmbiguous ? false : testDirty}
      retrying={testRequestAmbiguous}
      locked={testRequestAmbiguous}
      retainedValues={testRequestAmbiguous ? pendingTest.current?.values : repairValues}
      subjectDeclaration={testDefinitionValues?.subject_declaration ?? values?.subject_declaration ?? ""}
      onSubmit={requestTest}
      onStarted={(runId) => {
        const revision = pendingTest.current?.revision ?? testDefinitionValues?.definition.revision ?? 0;
        pendingTest.current = null;
        if (repairAttempt || formSurface.current?.formIsDirty) {
          setStartedTest({ id: runId, revision });
        }
        else void navigate({ to: routeHref("workflows.run", { id: runId }) });
      }}
    />
  </>);
}

function DefinitionActions({ context, history, publishState, sourceLoading, issues, onPublish, onTest }: {
  context: RecordToolbarContext;
  history: ReturnType<typeof useDefinitionHistory>;
  publishState: { fetching: boolean };
  sourceLoading: boolean;
  issues: number;
  onPublish: (context: RecordToolbarContext) => Promise<void>;
  onTest: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const publishReason = issues ? t(issues === 1 ? "form.publishBlockedOne" : "form.publishBlockedMany", { count: issues }) : undefined;
  return <><Button type="button" size="sm" variant="ghost" disabled={!history.canUndo} onClick={history.undo}>{t("form.undo")}</Button><Button type="button" size="sm" variant="ghost" disabled={!history.canRedo} onClick={history.redo}>{t("form.redo")}</Button><Button type="button" size="sm" variant="secondary" disabled={sourceLoading || context.form.pending} onClick={onTest}>{t("form.test")}</Button><Button
    type="button"
    size="sm"
    variant="secondary"
    loading={publishState.fetching}
    disabled={context.form.formIsDirty || sourceLoading || issues > 0}
    title={publishReason}
    onClick={() => { void onPublish(context); }}
  >{t("form.publish")}</Button></>;
}

function newTestRequestKey(): string {
  return globalThis.crypto.randomUUID();
}

function workflowFields(t: ReturnType<typeof useWorkflowsT>, subjectOptions: readonly { value: string; label: string }[]): React.ReactElement {
  return <>
    <Field name="name" title />
    <Field name="description" />
    <Group label={t("form.definition")} columns={2}>
      <Field name="status" readOnly widget="statusbar" />
      <Field name="version" readOnly />
      <Field name="lineage_id" label={t("form.lineage")} readOnly />
      <Field name="subject_declaration" label={t("form.subjectDeclaration")} description={t("form.subjectDeclarationDescription")} widget="select" options={subjectOptions} />
      <Field name="error_workflow" />
      <Field name="max_steps" />
    </Group>
    <Field name="budget" widget="json" />
  </>;
}

export const workflowDefinitionForm = registerForm(WORKFLOW_MODEL, WorkflowDefinitionForm);

function acceptedDefinition(
  submitted: WorkflowDefinitionValues,
  payload: {
    revision?: number | null;
    nodes: readonly { client_key: string; id: string }[];
    edges: readonly { client_key: string; id: string }[];
    diagnostics: WorkflowDefinitionValues["definition"]["readiness"];
  },
): WorkflowDefinitionValues {
  const nodes = { ...submitted.definition.nodes };
  for (const correlation of payload.nodes) {
    const node = nodes[correlation.client_key];
    if (node) nodes[correlation.client_key] = { ...node, id: correlation.id };
  }
  const edges = { ...submitted.definition.edges };
  for (const correlation of payload.edges) {
    const edge = edges[correlation.client_key];
    if (edge) edges[correlation.client_key] = { ...edge, id: correlation.id };
  }
  return {
    ...submitted,
    definition: {
      revision: payload.revision ?? submitted.definition.revision,
      nodes,
      edges,
      readiness: payload.diagnostics,
    },
  };
}

function reconcileDefinition({ accepted, submitted, current }: {
  accepted: Record<string, unknown>;
  submitted: Record<string, unknown>;
  current: Record<string, unknown>;
}): WorkflowDefinitionValues {
  const saved = accepted as WorkflowDefinitionValues;
  const sent = submitted as WorkflowDefinitionValues;
  const live = current as WorkflowDefinitionValues;
  const next = { ...saved };
  for (const key of Object.keys(live)) {
    if (key !== "definition" && !equal(live[key], sent[key])) next[key] = live[key];
  }
  return {
    ...next,
    definition: {
      ...saved.definition,
      nodes: reconcileRows(saved.definition.nodes, sent.definition.nodes, live.definition.nodes),
      edges: reconcileRows(saved.definition.edges, sent.definition.edges, live.definition.edges),
    },
  };
}

function reconcileRows<T extends Record<string, unknown>>(
  accepted: Record<string, T>,
  submitted: Record<string, T>,
  current: Record<string, T>,
): Record<string, T> {
  return Object.fromEntries(Object.entries(current).map(([key, live]) => {
    const sent = submitted[key];
    const saved = accepted[key];
    if (!sent || !saved) return [key, live];
    return [key, Object.fromEntries([...new Set([...Object.keys(saved), ...Object.keys(live)])].map((field) => [
      field,
      equal(live[field], sent[field]) ? saved[field] : live[field],
    ])) as T];
  }));
}

function definitionFailure(payload: { status?: string; current_revision?: number | null; diagnostics?: readonly DefinitionDiagnostic[] } | null | undefined, t: ReturnType<typeof useWorkflowsT>): string {
  if (payload?.status === "STALE") return t("form.staleDefinition");
  return payload?.diagnostics?.map(formatDiagnostic).join(" ") || t("form.definitionSaveFailed");
}

interface DefinitionDiagnostic {
  message: string;
  kind?: string | null;
  id?: string | null;
  client_key?: string | null;
  field?: string | null;
}

function formatDiagnostic(diagnostic: DefinitionDiagnostic): string {
  const identity = diagnostic.client_key || diagnostic.id;
  const location = [diagnostic.kind?.toLowerCase(), identity, diagnostic.field].filter(Boolean).join(".");
  return location ? `${location}: ${diagnostic.message}` : diagnostic.message;
}

function definitionSubmitError(payload: { status?: string; diagnostics?: readonly DefinitionDiagnostic[] } | null | undefined, values: WorkflowDefinitionValues, t: ReturnType<typeof useWorkflowsT>): unknown {
  if (payload?.status === "STALE") return new Error(definitionFailure(payload, t));
  const validationErrors: Record<string, string[]> = {};
  const formErrors: string[] = [];
  for (const diagnostic of payload?.diagnostics ?? []) {
    const path = diagnosticPath(diagnostic, values);
    if (path) validationErrors[path] = [...(validationErrors[path] ?? []), diagnostic.message];
    else formErrors.push(formatDiagnostic(diagnostic));
  }
  if (Object.keys(validationErrors).length === 0 && formErrors.length === 0) return new Error(definitionFailure(payload, t));
  return {
    message: t("form.definitionValidationFailed"),
    response: { errors: [{ message: t("form.definitionValidationFailed"), extensions: { validationErrors, formErrors } }] },
  };
}

function diagnosticPath(diagnostic: DefinitionDiagnostic, values: WorkflowDefinitionValues): string | null {
  if (!diagnostic.field) return null;
  if (diagnostic.kind?.toUpperCase() === "WORKFLOW") return diagnostic.field;
  const rows = diagnostic.kind?.toUpperCase() === "NODE" ? values.definition.nodes
    : diagnostic.kind?.toUpperCase() === "EDGE" ? values.definition.edges : null;
  if (!rows) return null;
  const identity = Object.entries(rows).find(([key, row]) => (
    key === diagnostic.client_key || key === diagnostic.id || row.id === diagnostic.id || row.clientKey === diagnostic.client_key
  ))?.[0];
  return identity ? `definition.${diagnostic.kind?.toLowerCase()}s.${identity}.${diagnostic.field}` : null;
}

const equal = definitionValueEqual;

interface DefinitionChange { key: string; label: string; local: unknown; remote: unknown }
interface DefinitionReviewLabels { workflow: Readonly<Record<string, string>>; node: Readonly<Record<string, string>>; edge: Readonly<Record<string, string>> }
function definitionChanges(remote: WorkflowDefinitionValues | null, local: WorkflowDefinitionValues, labels: DefinitionReviewLabels, t: ReturnType<typeof useWorkflowsT>): DefinitionChange[] {
  if (!remote) return [{ key: "unavailable", label: t("form.latestUnavailable"), local: "", remote: "" }];
  const changes: DefinitionChange[] = [];
  for (const field of WORKFLOW_REVIEW_FIELDS) if (!equal(remote[field], local[field])) changes.push({ key: `workflow.${field}`, label: t("form.change.workflowField", { field: labels.workflow[field] ?? field }), local: local[field], remote: remote[field] });
  for (const key of new Set([...Object.keys(remote.definition.nodes), ...Object.keys(local.definition.nodes)])) {
    const localNode = local.definition.nodes[key]; const remoteNode = remote.definition.nodes[key];
    const label = localNode?.name || remoteNode?.name || localNode?.key || remoteNode?.key || key;
    if (!localNode || !remoteNode) changes.push({ key: `node.${key}`, label: t("form.change.step", { label }), local: localNode ? t("form.change.added") : t("form.change.removed"), remote: remoteNode ? t("form.change.added") : t("form.change.removed") });
    else for (const field of NODE_REVIEW_FIELDS) if (!equal(localNode[field], remoteNode[field])) changes.push({ key: `node.${key}.${field}`, label: t("form.change.stepField", { label, field: labels.node[field] ?? field }), local: localNode[field], remote: remoteNode[field] });
  }
  for (const key of new Set([...Object.keys(remote.definition.edges), ...Object.keys(local.definition.edges)])) {
    const localEdge = local.definition.edges[key]; const remoteEdge = remote.definition.edges[key];
    const edge = localEdge ?? remoteEdge; const label = edge ? `${edge.source} → ${edge.target}` : key;
    if (!localEdge || !remoteEdge) changes.push({ key: `edge.${key}`, label: t("form.change.connection", { label }), local: localEdge ? t("form.change.added") : t("form.change.removed"), remote: remoteEdge ? t("form.change.added") : t("form.change.removed") });
    else for (const field of EDGE_REVIEW_FIELDS) if (!equal(localEdge[field], remoteEdge[field])) changes.push({ key: `edge.${key}.${field}`, label: t("form.change.connectionField", { label, field: labels.edge[field] ?? field }), local: localEdge[field], remote: remoteEdge[field] });
  }
  return changes.length ? changes : [{ key: "revision", label: t("form.change.revision"), local: t("form.change.unchanged"), remote: t("form.change.newer") }];
}

const WORKFLOW_REVIEW_FIELDS = ["name", "description", "purpose", "subject_declaration", "error_workflow", "max_steps", "budget"] as const;
const NODE_REVIEW_FIELDS = ["key", "name", "step_class", "config", "join_rule", "is_entry", "position"] as const;
const EDGE_REVIEW_FIELDS = ["source", "target", "condition"] as const;

function displayValue(value: unknown, t: ReturnType<typeof useWorkflowsT>): string {
  if (value === undefined) return t("form.change.notSet");
  if (value === null) return t("form.change.empty");
  return typeof value === "string" ? value : JSON.stringify(value);
}

function useDefinitionReviewLabels(): DefinitionReviewLabels {
  const workflow = useModelMetadata(WORKFLOW_MODEL);
  const step = useModelMetadata("workflows.Step");
  const edge = useModelMetadata("workflows.Edge");
  return React.useMemo(() => ({
    workflow: descriptorLabels(WORKFLOW_REVIEW_FIELDS, workflow),
    node: descriptorLabels(NODE_REVIEW_FIELDS, step),
    edge: descriptorLabels(EDGE_REVIEW_FIELDS, edge),
  }), [edge, step, workflow]);
}

function descriptorLabels(fields: readonly string[], metadata: Parameters<typeof fieldsWithMetadataDefaults>[1]): Readonly<Record<string, string>> {
  return Object.fromEntries(fieldsWithMetadataDefaults(fields.map((name) => ({ name })), metadata).map((field) => [field.name, typeof field.label === "string" ? field.label : field.name]));
}
