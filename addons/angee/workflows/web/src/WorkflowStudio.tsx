import * as React from "react";
import * as v from "valibot";
import { useAuthoredMutation, useAuthoredQuery, stableSerialize } from "@angee/refine";
import type { DocumentType } from "@angee/gql/console";
import {
  ActionFormProvider, Alert, Button, DescriptorFieldList, Dialog, ErrorBanner, GraphEditor,
  JsonValueSchema, LoadingPanel, PageAside, PrimaryPanePublisher, RailPanel, applyFormErrors, createKeyedEntry,
  errorMessage, useActionForm, useActionFormValues, useChatterContent, useFormHistory,
  useFormSpecFields, useRuntimeViewAs, wireFormSubmitResult,
  type FormSpecFieldDescriptor, type GraphEditorLink, type GraphEditorSelection, type GraphViewPosition,
  type UseActionFormResult,
} from "@angee/ui";

import { PublishWorkflowDocument, SaveWorkflowDraftDocument, WorkflowStepPortsDocument, WorkflowStudioDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { DiagnosticsProjection, OutcomesProjection, studioErrors, studioSnapshot, studioValues, type StudioValues, type StudioNode } from "./studio-state";

const MODEL = "workflows.Workflow";
type StudioRead = DocumentType<typeof WorkflowStudioDocument>;
type Choice = StudioRead["workflow_step_choices"][number];
type Acknowledgement = NonNullable<DocumentType<typeof SaveWorkflowDraftDocument>["save_workflow_draft"]["data"]>;
type Form = UseActionFormResult<StudioValues>["form"];
type AddIntent = { from?: string; port?: string; link?: GraphEditorLink; position?: GraphViewPosition };
const EMPTY_SELECTION: GraphEditorSelection = { nodes: [], link: null };
const ConfigDefaults = v.object({ config: v.optional(v.record(v.string(), v.unknown()), {}) });
const emptyConfigSchema = { type: "object", properties: {} };

/** Load writer-only authoring data without replacing a mounted user's draft. */
export function WorkflowStudio({ recordId, active = true }: { recordId: string; active?: boolean }): React.ReactElement {
  const t = useWorkflowsT();
  const read = useAuthoredQuery(WorkflowStudioDocument, { id: recordId, target: recordId }, { models: [MODEL], enabled: active });
  if (!read.data) return read.error
    ? <ErrorBanner description={errorMessage(read.error, t("studio.loadFailed"))}
        actions={<Button onClick={() => void read.refetch()}>{t("studio.reload")}</Button>} />
    : <LoadingPanel message={t("studio.loading")} />;
  if (read.data.workflow_by_pk?.draft == null || read.data.workflow_by_pk.draft_revision == null) {
    return <ErrorBanner description={t("studio.unavailable")} />;
  }
  return <StudioSession key={recordId} recordId={recordId} initial={read.data} active={active}
    loadLatest={async () => {
      const latest = await read.refetch();
      if (latest.error) throw latest.error;
      if (!latest.data) throw new Error(t("studio.loadFailed"));
      return latest.data;
    }} />;
}

/** Compose native form state, shared submit/history, and the controlled graph. */
function StudioSession({ recordId, initial, loadLatest, active }: {
  recordId: string; initial: StudioRead; loadLatest: () => Promise<StudioRead>; active: boolean;
}): React.ReactElement {
  const t = useWorkflowsT();
  const preview = useRuntimeViewAs();
  const [selection, setSelection] = React.useState(EMPTY_SELECTION);
  const [intent, setIntent] = React.useState<AddIntent | null>(null);
  const [version, setVersion] = React.useState(initial.workflow_by_pk?.published?.number);
  const [dependents, setDependents] = React.useState<readonly string[]>([]);
  const [loadingLatest, setLoadingLatest] = React.useState(false);
  const revision = React.useRef(initial.workflow_by_pk!.draft_revision!);
  const conflictRevision = React.useRef<number | null>(null);
  const savedKeys = React.useRef(new Map<string, string>(studioValues(initial.workflow_by_pk!.draft, initial.workflow_by_pk!.layout)
    .entries.map((entry) => [entry.key, entry.clientId])));
  const [save] = useAuthoredMutation(SaveWorkflowDraftDocument, {
    invalidateModels: [MODEL], shouldInvalidate: (data) => data?.save_workflow_draft.status === "OK",
  });
  const [publish, publication] = useAuthoredMutation(PublishWorkflowDocument, {
    invalidateModels: [MODEL, "workflows.WorkflowVersion"], shouldInvalidate: (data) => data?.publish_workflow.status === "OK",
  });
  const action: UseActionFormResult<StudioValues> = useActionForm<StudioValues, Acknowledgement>({
    defaultValues: studioValues(initial.workflow_by_pk!.draft, initial.workflow_by_pk!.layout),
    fieldNames: ["entries"],
    genericErrorMessage: t("studio.saveFailed"),
    submit: async (values) => {
      const snapshot = studioSnapshot(values);
      if (snapshot.status !== "ok") return snapshot;
      const response = await save({ id: recordId, draft: snapshot.data.draft, layout: snapshot.data.layout, revision: revision.current });
      if (!response) throw new Error(t("studio.saveFailed"));
      const wire = response.save_workflow_draft;
      if (wire.status === "CONFLICT") conflictRevision.current = wire.data?.revision ?? null;
      const result = wireFormSubmitResult(wire);
      if (result.status === "invalid") return { ...result, issues: studioErrors(result.issues, action.form.getValues().entries, snapshot.data.clientIdByKey) };
      if (result.status === "ok") savedKeys.current = new Map(snapshot.data.clientIdByKey);
      return result;
    },
    onSuccess: (values, acknowledgement) => {
      revision.current = acknowledgement.revision;
      conflictRevision.current = null;
      action.form.reset(values);
      history.reset();
      const diagnostics = v.parse(DiagnosticsProjection, acknowledgement.diagnostics);
      const fields: Record<string, string[]> = {};
      const messages: string[] = [];
      for (const issue of diagnostics) {
        if (issue.path.length) (fields[issue.path.join(".")] ??= []).push(issue.message);
        else messages.push(issue.message);
      }
      if (diagnostics.length) applyFormErrors(action.form, { status: "invalid",
        issues: studioErrors({ fieldErrors: fields, formErrors: messages }, values.entries, savedKeys.current),
      }, { fieldNames: ["entries"], fieldSummary: () => t("studio.savedWithIssues") });
    },
  });
  const disabled = !active || action.submitting || publication.fetching || loadingLatest || Boolean(preview.viewAs || preview.pending);
  const history = useFormHistory(action.form, { readOnly: disabled });
  const topology = useActionFormValues(action.form, (values) => JSON.stringify(values.entries.map(({ clientId, key, value }) => ({
    clientId, key, step: value.step, label: value.label, next: value.next,
  }))));
  const entryIds = React.useMemo(() => action.form.getValues().entries.map((entry) => entry.clientId), [topology, action.form]);
  const index = entryIds.indexOf(selection.nodes[0] ?? "");
  const update = React.useCallback((change: (values: StudioValues) => void) => {
    history.perform(() => {
      const values = structuredClone(action.form.getValues());
      change(values);
      action.form.setValue("entries", values.entries, { shouldDirty: true });
      action.form.setValue("layout", values.layout, { shouldDirty: true });
      action.form.setValue("document", values.document, { shouldDirty: true });
    });
  }, [action.form, history.perform]);
  const add = React.useCallback((choice: Choice) => {
    const origin = intent;
    update((values) => {
      const keys = new Set(values.entries.map((entry) => entry.key));
      let key = choice.key;
      for (let number = 2; keys.has(key); number++) key = `${choice.key}_${number}`;
      const entry = createKeyedEntry<StudioNode>({ step: choice.key, label: choice.label,
        config: v.parse(ConfigDefaults, choice.defaults).config, next: {} }, key);
      values.entries.push(entry);
      values.layout = { ...values.layout, [entry.clientId]: origin?.position ?? { x: values.entries.length * 220, y: 80 } };
      if (origin?.from && origin.port) {
        const parent = values.entries.find((node) => node.clientId === origin.from);
        if (parent) {
          const targets = parent.value.next[origin.port] ?? [];
          const list = typeof targets === "string" ? [targets] : targets;
          parent.value.next[origin.port] = [...list.filter((id) => id !== origin.link?.to), entry.clientId];
        }
        if (origin.link) {
          const outcomes = v.parse(OutcomesProjection, choice.outcomes);
          const port = Object.keys(outcomes).find((name) => name !== "error");
          if (port) entry.value.next[port] = [origin.link.to];
        }
      }
      setSelection({ nodes: [entry.clientId], link: null });
    });
    setIntent(null);
  }, [intent, update]);
  const palette = React.useMemo(() => <RailPanel title={t("studio.palette")}>
    <div className="grid gap-2 p-3">{initial.workflow_step_choices.filter((choice) => !choice.internal).map((choice) =>
      <Button key={choice.key} type="button" disabled={disabled} variant="ghost" onClick={() => add(choice)}>{choice.label}</Button>)}</div>
  </RailPanel>, [initial.workflow_step_choices, disabled, add, t]);
  const inspector = React.useMemo(() => ({ tabs: [{ id: "workflow-inspector", label: t("studio.inspector"), icon: "settings",
    children: <ActionFormProvider {...action.form}><PageAside collapse="never" gutter="compact" className="h-full w-full border-l-0">
      <RailPanel title={t("studio.inspector")} empty={t("studio.selectNode")}>
        {index < 0 ? null : <NodeInspector index={index} form={action.form} choices={initial.workflow_step_choices}
          disabled={disabled} start={history.start} commit={history.commit} />}
      </RailPanel>
    </PageAside></ActionFormProvider>,
  }] }), [action.form, index, initial.workflow_step_choices, disabled, history.start, history.commit, t]);
  useChatterContent(active ? inspector : null);

  async function discard(): Promise<void> {
    setLoadingLatest(true);
    try {
      const latest = await loadLatest();
      const record = latest.workflow_by_pk;
      if (record?.draft == null || record.draft_revision == null) throw new Error(t("studio.unavailable"));
      action.form.reset(studioValues(record.draft, record.layout));
      revision.current = record.draft_revision;
      conflictRevision.current = null;
      savedKeys.current = new Map(action.form.getValues().entries.map((entry) => [entry.key, entry.clientId]));
      setVersion(record.published?.number);
      history.reset();
    } catch (cause) { action.form.setError("root.server", {
      type: conflictRevision.current == null ? "server" : "conflict", message: errorMessage(cause, t("studio.loadFailed")),
    }); }
    finally { setLoadingLatest(false); }
  }
  async function publishSaved(): Promise<void> {
    if (disabled || action.form.formState.isDirty) return;
    action.resetErrors();
    try {
      const response = await publish({ id: recordId });
      if (!response) throw new Error(t("studio.publishFailed"));
      const result = wireFormSubmitResult(response.publish_workflow);
      if (result.status === "invalid") result.issues = studioErrors(result.issues, action.form.getValues().entries, savedKeys.current);
      if (applyFormErrors(action.form, result, { fieldNames: ["entries"], fieldSummary: () => t("studio.publishIssues") })) return;
      setVersion(result.data.number);
      setDependents(result.data.dependents);
    } catch (cause) { action.form.setError("root.server", { type: "server", message: errorMessage(cause, t("studio.publishFailed")) }); }
  }
  return <ActionFormProvider {...action.form}>
    <PrimaryPanePublisher node={active ? palette : null} />
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 p-3">
        <Button type="button" disabled={disabled || !history.canUndo} onClick={history.undo}>{t("studio.undo")}</Button>
        <Button type="button" disabled={disabled || !history.canRedo} onClick={history.redo}>{t("studio.redo")}</Button>
        <Button type="button" disabled={disabled} onClick={() => setIntent({})}>{t("studio.addStep")}</Button>
        <Button type="button" disabled={disabled || !action.form.formState.isDirty} onClick={() => void action.run()}>{t("studio.save")}</Button>
        <Button type="button" disabled={disabled || action.form.formState.isDirty || action.saveConflict} onClick={() => void publishSaved()}>{t("studio.publish")}</Button>
        <span className="text-sm text-fg-muted">{version == null ? t("studio.unpublished") : t("studio.version", { number: version })}</span>
      </div>
      {action.formError ? <ErrorBanner description={action.formError} /> : null}
      {action.saveConflict ? <Alert tone="warning"><div className="flex flex-wrap gap-2">
        <Button type="button" disabled={disabled || conflictRevision.current == null} onClick={() => {
          if (conflictRevision.current == null) return;
          revision.current = conflictRevision.current;
          void action.run();
        }}>{t("studio.overwrite")}</Button>
        <Button type="button" disabled={disabled} onClick={() => void discard()}>{t("studio.discard")}</Button>
      </div></Alert> : null}
      {dependents.length ? <Alert tone="info">{t("studio.dependents", { names: dependents.join(", ") })}</Alert> : null}
      <StudioGraph form={action.form} recordId={recordId} topology={topology} selection={selection}
        select={setSelection} active={active} disabled={disabled} update={update} add={setIntent} />
    </div>
    <Dialog.Root open={intent !== null} onOpenChange={(open) => { if (!open) setIntent(null); }}>
      <Dialog.Portal><Dialog.Backdrop /><Dialog.Content>
        <Dialog.Header><Dialog.Title>{t("studio.palette")}</Dialog.Title></Dialog.Header>
        <Dialog.Body>{palette}</Dialog.Body>
        <Dialog.Footer><Dialog.Close>{t("studio.close")}</Dialog.Close></Dialog.Footer>
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </ActionFormProvider>;
}

function NodeInspector({ index, form, choices, disabled, start, commit }: {
  index: number; form: Form; choices: readonly Choice[]; disabled: boolean;
  start: (group: string) => void; commit: (group: string) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const entry = useActionFormValues(form, (values) => values.entries[index]);
  const choice = choices.find((item) => item.key === entry?.value.step);
  const config = useFormSpecFields(choice?.config_schema ?? emptyConfigSchema);
  const prefix = `entries.${index}`;
  const fields: readonly FormSpecFieldDescriptor[] = [
    { name: `${prefix}.key`, label: t("studio.key"), widget: "text" },
    { name: `${prefix}.value.label`, label: t("studio.label"), widget: "text" },
    { name: `${prefix}.value.config`, label: t("studio.config"), widget: "object", objectTemplate: config },
  ];
  return <div className="grid gap-4 p-3" onFocusCapture={() => start(prefix)}
    onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) commit(prefix); }}>
    <DescriptorFieldList fields={fields} readOnly={disabled} />
  </div>;
}

const StructureProjection = v.array(v.object({
  clientId: v.string(), key: v.string(), step: v.string(), label: v.string(),
  next: v.record(v.string(), v.union([v.string(), v.array(v.string())])),
}));
/** Graph topology is subscribed separately from configuration and layout. */
function StudioGraph({ form, recordId, topology, selection, select, active, disabled, update, add }: {
  form: Form; recordId: string; topology: string; selection: GraphEditorSelection;
  select: (value: GraphEditorSelection) => void; disabled: boolean; active: boolean;
  update: (change: (values: StudioValues) => void) => void; add: (intent: AddIntent) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const configurations = useActionFormValues(form, (values) => values.entries.map((entry) => ({
    node: entry.clientId, step: entry.value.step, config: v.parse(JsonValueSchema, entry.value.config),
  })));
  const ports = useAuthoredQuery(WorkflowStepPortsDocument, { id: recordId, configurations }, { models: [MODEL], enabled: active });
  const portKey = stableSerialize(ports.data?.workflow_step_ports ?? []);
  const layout = useActionFormValues(form, (values) => values.layout);
  const errors = useActionFormValues(form, (values) => values.entries.map((entry) => entry.clientId));
  const graph = React.useMemo(() => {
    const entries = v.parse(StructureProjection, JSON.parse(topology));
    const outcomes = new Map((ports.data?.workflow_step_ports ?? []).map((entry) => [entry.node, v.parse(OutcomesProjection, entry.outcomes)]));
    return {
      nodes: entries.map((entry) => ({ id: entry.clientId, kind: entry.step, title: entry.label || entry.key,
        code: entry.key, ports: Object.entries(outcomes.get(entry.clientId) ?? {}).map(([id, label]) => ({ id, label })) })),
      links: entries.flatMap((entry) => Object.entries(entry.next).flatMap(([port, targets]) =>
        (typeof targets === "string" ? [targets] : targets).map((to) => ({ from: entry.clientId, port, to })))),
    };
  }, [topology, portKey]);
  const status = Object.fromEntries(errors.flatMap((id, index) =>
    form.getFieldState(`entries.${index}`).invalid ? [[id, { label: t("studio.issues"), tone: "danger" as const }]] : []));
  function link(link: GraphEditorLink, remove = false): void {
    update((values) => {
      const source = values.entries.find((entry) => entry.clientId === link.from);
      if (!source) return;
      const targets = source.value.next[link.port] ?? [];
      const next = (typeof targets === "string" ? [targets] : targets).filter((id) => id !== link.to);
      if (!remove) next.push(link.to);
      if (next.length) source.value.next[link.port] = next;
      else delete source.value.next[link.port];
    });
  }
  return <div className="flex min-h-[32rem] flex-1 flex-col">
    {ports.error ? <ErrorBanner description={errorMessage(ports.error, t("studio.portsFailed"))} /> : null}
    <GraphEditor {...graph} layout={layout} selected={selection} onSelectionChange={select} readOnly={disabled || !ports.data}
      className="flex-1" status={status} canLink={(from, _port, to) => {
        const visited = new Set<string>();
        const reaches = (id: string): boolean => id === from || (!visited.has(id) && (visited.add(id), graph.links.some((edge) => edge.from === id && reaches(edge.to))));
        return !reaches(to);
      }} onLink={(value) => link(value)} onUnlink={(value) => link(value, true)}
      onLayoutChange={(value) => update((values) => { values.layout = value; })}
      onDelete={(ids) => update((values) => {
        values.entries = values.entries.filter((entry) => !ids.includes(entry.clientId));
        for (const entry of values.entries) for (const [port, targets] of Object.entries(entry.value.next)) {
          const next = (typeof targets === "string" ? [targets] : targets).filter((id) => !ids.includes(id));
          if (next.length) entry.value.next[port] = next;
          else delete entry.value.next[port];
        }
        values.layout = Object.fromEntries(Object.entries(values.layout).filter(([id]) => !ids.includes(id)));
        // Results are binding references, so deleting their producer also
        // removes its output declaration. Other binding errors remain located
        // by Definition.check rather than guessing a replacement producer.
        if (Array.isArray(values.document.results)) values.document.results = values.document.results.filter((binding: unknown) => {
          const result = v.parse(v.looseObject({ from: v.optional(v.string()) }), binding);
          return !result.from || !ids.includes(result.from.split(".")[0]!);
        });
      })} onAddFromPort={(from, port, position) => add({ from, port, position })}
      onInsertOnLink={(value, position) => add({ from: value.from, port: value.port, link: value, position })} />
  </div>;
}
