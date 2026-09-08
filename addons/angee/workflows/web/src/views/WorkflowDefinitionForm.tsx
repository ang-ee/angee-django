import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Button,
  ErrorBanner,
  Field,
  Form,
  Group,
  LoadingPanel,
  acknowledgeFormSubmit,
  registerForm,
  type FormSubmit,
  type FormViewAcknowledgedSource,
  type RecordToolbarContext,
  type RegisteredFormProps,
  useRouteHref,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import {
  PublishWorkflowDefinitionDocument,
  SaveWorkflowDefinitionDocument,
  WorkflowDefinitionDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import {
  definitionEdit,
  definitionValueEqual,
  definitionValues,
  type WorkflowDefinitionValues,
} from "./workflow-definition-state";

export const WORKFLOW_MODEL = "workflows.Workflow";

export function WorkflowDefinitionForm(props: RegisteredFormProps): React.ReactElement {
  return props.id == null ? <WorkflowCreateForm {...props} /> : <WorkflowDefinitionEditForm key={String(props.id)} {...props} />;
}

function WorkflowCreateForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  return <Form {...props} resource={WORKFLOW_MODEL}><Field name="name" title /></Form>;
}

function WorkflowDefinitionEditForm({ resource: _resource, id, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useWorkflowsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const definition = useAuthoredQuery(
    WorkflowDefinitionDocument,
    { workflow: id ?? "" },
    { models: [WORKFLOW_MODEL, "workflows.Step", "workflows.Edge"] },
  );
  const [saveDefinition] = useAuthoredMutation(SaveWorkflowDefinitionDocument, {
    invalidateModels: [WORKFLOW_MODEL, "workflows.Step", "workflows.Edge"],
  });
  const [publishDefinition, publishState] = useAuthoredMutation(PublishWorkflowDefinitionDocument, {
    invalidateModels: [WORKFLOW_MODEL],
  });
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
  const admitNextSnapshot = React.useRef(false);
  React.useEffect(() => {
    if (!snapshot || !projected || snapshot.workflow.id !== id) return;
    setAcknowledged((current) => {
      if (current && current.record.id === snapshot.workflow.id && !admitNextSnapshot.current) return current;
      admitNextSnapshot.current = false;
      return { record: snapshot.workflow, values: projected };
    });
  }, [id, projected, snapshot]);
  const values = acknowledged?.values ?? null;
  const source = React.useMemo<FormViewAcknowledgedSource>(() => ({
    record: acknowledged?.record ?? null,
    values,
    loading: definition.isFetching,
    reload: () => {
      admitNextSnapshot.current = true;
      void definition.refetch();
    },
  }), [acknowledged?.record, definition.isFetching, definition.refetch, values]);
  const readOnly = props.readOnly || (acknowledged !== null && String(acknowledged.record.status) !== "DRAFT");

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
      throw definitionSubmitError(payload, submitted);
    }
    const accepted = acceptedDefinition(submitted, payload);
    const { definition: _definition, ...acceptedWorkflow } = accepted;
    const record = { ...(acknowledged?.record ?? {}), ...acceptedWorkflow };
    setAcknowledged({ record, values: accepted });
    setStale(false);
    setStaleReview(null);
    setReviewOpen(false);
    setReviewCandidate(null);
    return acknowledgeFormSubmit(
      record,
      accepted,
      reconcileDefinition,
    );
  }, [acknowledged?.record, id, saveDefinition]);

  const reviewLatest = React.useCallback(async () => {
    setReloadError(null);
    const current = formSurface.current?.form.getValues() as WorkflowDefinitionValues | undefined;
    if (current) setStaleReview(current);
    try {
      const refreshed = await definition.refetch();
      const next = refreshed.data?.workflow_definition;
      if (!next || next.workflow.id !== id) throw new Error("The latest workflow could not be loaded.");
      setReviewCandidate({ record: next.workflow, values: definitionValues(next) });
      setReviewOpen(true);
    } catch (error) {
      setReloadError(definitionFailureMessage(error));
    }
  }, [definition.refetch, id]);

  const discardAndReload = React.useCallback(() => {
    if (!reviewCandidate) return;
    setAcknowledged(reviewCandidate);
    setStale(false);
    setStaleReview(null);
    setReviewOpen(false);
    setReviewCandidate(null);
    setReloadError(null);
    setFormGeneration((generation) => generation + 1);
  }, [reviewCandidate]);

  const publish = React.useCallback(async (context: RecordToolbarContext) => {
    if (!id || acknowledged?.record.id !== id) return;
    const current = context.form.form.getValues() as WorkflowDefinitionValues;
    try {
      const result = await publishDefinition({
        workflow: id,
        expectedRevision: current.definition.revision,
      });
      const payload = result?.publish_workflow_definition;
      if (!payload || payload.status !== "SUCCESS") throw new Error(definitionFailure(payload));
      context.reload();
    } catch (error) {
      context.form.form.setError("root.server", { type: "server", message: definitionFailureMessage(error) });
    }
  }, [acknowledged?.record, id, publishDefinition]);

  if (!acknowledged) {
    return definition.error
      ? <ErrorBanner description={definitionFailureMessage(definition.error)} />
      : <LoadingPanel message={t("canvas.loading")} />;
  }

  return (<>
    {stale ? <div className="grid gap-2"><ErrorBanner description={t("form.staleDefinition")} /><ErrorBanner description={!reviewOpen ? reloadError : null} /><Button type="button" size="sm" variant="secondary" onClick={() => { void reviewLatest(); }}>{t("form.reviewDefinition")}</Button></div> : null}
    {reviewOpen && staleReview ? <section className="grid gap-2 border-b border-border-subtle p-4" aria-label={t("form.staleReview")}>
      <strong>{t("form.staleReview")}</strong>
      <div className="grid gap-2 text-sm">{definitionChanges(reviewCandidate?.values ?? null, staleReview).map((change) => <div key={change.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2"><strong>{change.label}</strong><span><small>{t("form.yourEdits")}</small><br />{displayValue(change.local)}</span><span><small>{t("form.latestSaved")}</small><br />{displayValue(change.remote)}</span></div>)}</div>
      <ErrorBanner description={reloadError} />
      <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => setReviewOpen(false)}>{t("form.cancelReview")}</Button><Button type="button" size="sm" variant="danger" disabled={!reviewCandidate} onClick={discardAndReload}>{t("form.discardReload")}</Button></div>
    </section> : null}
    <Form
      key={formGeneration}
      {...props}
      resource={WORKFLOW_MODEL}
      id={id}
      readOnly={readOnly}
      acknowledgedSource={source}
      submit={submit}
      toolbarStart={(context) => {
        formSurface.current = context.form;
        return readOnly ? <Button type="button" size="sm" variant="secondary" onClick={() => {
          if (values?.lineage_id) void navigate({ to: routeHref("workflows.workflow", { id: values.lineage_id }) });
        }}>{t("form.openDraft")}</Button> : <Button
            type="button"
            size="sm"
            variant="secondary"
            loading={publishState.fetching}
            disabled={context.form.formIsDirty || source.loading || values == null}
            onClick={() => { void publish(context); }}
          >
            {t("form.publish")}
          </Button>;
      }}
    >
      {workflowFields(t)}
      {props.children}
    </Form>
  </>);
}

function workflowFields(t: ReturnType<typeof useWorkflowsT>): React.ReactElement {
  return <>
    <Field name="name" title />
    <Field name="description" />
    <Group label={t("form.definition")} columns={2}>
      <Field name="status" readOnly widget="statusbar" />
      <Field name="version" readOnly />
      <Field name="lineage_id" label={t("form.lineage")} readOnly />
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

function definitionFailure(payload: { status?: string; current_revision?: number | null; diagnostics?: readonly DefinitionDiagnostic[] } | null | undefined): string {
  if (payload?.status === "STALE") return "This draft changed elsewhere. Your edits are preserved.";
  return payload?.diagnostics?.map(formatDiagnostic).join(" ") || "The workflow definition could not be saved.";
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

function definitionFailureMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function definitionSubmitError(payload: { status?: string; diagnostics?: readonly DefinitionDiagnostic[] } | null | undefined, values: WorkflowDefinitionValues): unknown {
  if (payload?.status === "STALE") return new Error(definitionFailure(payload));
  const validationErrors: Record<string, string[]> = {};
  const formErrors: string[] = [];
  for (const diagnostic of payload?.diagnostics ?? []) {
    const path = diagnosticPath(diagnostic, values);
    if (path) validationErrors[path] = [...(validationErrors[path] ?? []), diagnostic.message];
    else formErrors.push(formatDiagnostic(diagnostic));
  }
  if (Object.keys(validationErrors).length === 0 && formErrors.length === 0) return new Error(definitionFailure(payload));
  return {
    message: "Workflow definition validation failed.",
    response: { errors: [{ message: "Workflow definition validation failed.", extensions: { validationErrors, formErrors } }] },
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
function definitionChanges(remote: WorkflowDefinitionValues | null, local: WorkflowDefinitionValues): DefinitionChange[] {
  if (!remote) return [{ key: "unavailable", label: "Latest version unavailable", local: "", remote: "" }];
  const changes: DefinitionChange[] = [];
  for (const field of WORKFLOW_REVIEW_FIELDS) if (!equal(remote[field], local[field])) changes.push({ key: `workflow.${field}`, label: `Workflow ${field}`, local: local[field], remote: remote[field] });
  for (const key of new Set([...Object.keys(remote.definition.nodes), ...Object.keys(local.definition.nodes)])) {
    const localNode = local.definition.nodes[key]; const remoteNode = remote.definition.nodes[key];
    const label = localNode?.name || remoteNode?.name || localNode?.key || remoteNode?.key || key;
    if (!localNode || !remoteNode) changes.push({ key: `node.${key}`, label: `Step ${label}`, local: localNode ? "Added" : "Removed", remote: remoteNode ? "Added" : "Removed" });
    else for (const field of NODE_REVIEW_FIELDS) if (!equal(localNode[field], remoteNode[field])) changes.push({ key: `node.${key}.${field}`, label: `Step ${label} · ${field}`, local: localNode[field], remote: remoteNode[field] });
  }
  for (const key of new Set([...Object.keys(remote.definition.edges), ...Object.keys(local.definition.edges)])) {
    const localEdge = local.definition.edges[key]; const remoteEdge = remote.definition.edges[key];
    const edge = localEdge ?? remoteEdge; const label = edge ? `${edge.source} → ${edge.target}` : key;
    if (!localEdge || !remoteEdge) changes.push({ key: `edge.${key}`, label: `Edge ${label}`, local: localEdge ? "Added" : "Removed", remote: remoteEdge ? "Added" : "Removed" });
    else for (const field of EDGE_REVIEW_FIELDS) if (!equal(localEdge[field], remoteEdge[field])) changes.push({ key: `edge.${key}.${field}`, label: `Edge ${label} · ${field}`, local: localEdge[field], remote: remoteEdge[field] });
  }
  return changes.length ? changes : [{ key: "revision", label: "Workflow revision", local: "Your values are unchanged", remote: "A newer revision exists" }];
}

const WORKFLOW_REVIEW_FIELDS = ["name", "description", "purpose", "subject_declaration", "error_workflow", "max_steps", "budget"] as const;
const NODE_REVIEW_FIELDS = ["key", "name", "step_class", "config", "join_rule", "is_entry", "position"] as const;
const EDGE_REVIEW_FIELDS = ["source", "target", "condition"] as const;

function displayValue(value: unknown): string {
  if (value === undefined) return "not set";
  if (value === null) return "empty";
  return typeof value === "string" ? value : JSON.stringify(value);
}
