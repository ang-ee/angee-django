import * as React from "react";
import * as v from "valibot";
import { publicGraphQLErrorsFromUnknown, useAuthoredMutation, useAuthoredQuery, stableSerialize } from "@angee/refine";
import type { DocumentType } from "@angee/gql/console";
import {
  ActionFormProvider, Alert, Button, DescriptorFieldList, Dialog, ErrorBanner, GraphEditor,
  JsonValueSchema, LoadingPanel, PageAside, PrimaryPanePublisher, RailPanel, applyFormErrors, createKeyedEntry,
  deserializeFormSpec, errorMessage, formSubmitError, savedFormSubmitResult, useActionForm, useAppRuntime, useWatch, useFormState, useChatter, useChatterContent, useFormHistory,
  useRuntimeViewAs, useUnsavedChangesNavigationGuard,
  type FormSpecFieldDescriptor, type GraphEditorLayout, type GraphEditorLink, type GraphEditorSelection, type GraphViewPosition,
  type UseActionFormResult, type ValidationErrors,
} from "@angee/ui";

import { PublishWorkflowDocument, SaveWorkflowDraftDocument, WorkflowStepOutcomesDocument, WorkflowStudioDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { EMPTY_ISSUES, OutcomesProjection, captureStudioIssues, diagnosticErrors, dropStudioIssue, projectStudioIssues, studioSnapshot, studioValues,
  type IssueOrigin, type StudioIssues, type StudioValues, type StudioNode } from "./studio-state";

const MODEL = "workflows.Workflow";
const INSPECTOR_TAB = "workflow-inspector";
type StudioRead = DocumentType<typeof WorkflowStudioDocument>;
type Choice = StudioRead["workflow_step_choices"][number];
type Acknowledgement = DocumentType<typeof SaveWorkflowDraftDocument>["save_workflow_draft"];
type Form = UseActionFormResult<StudioValues>["form"];
type AddIntent = { from?: string; port?: string; link?: GraphEditorLink; position?: GraphViewPosition };
const EMPTY_SELECTION: GraphEditorSelection = { nodes: [], link: null };
const ConfigDefaults = v.object({ config: v.optional(v.record(v.string(), v.unknown()), {}) });
const emptyConfigSchema = { type: "object", properties: {} };
const configurationsFor = (values: StudioValues) => values.entries.map((entry) => ({
  node: entry.clientId, step: entry.value.step, config: v.parse(JsonValueSchema, entry.value.config),
}));

/** Monitor readers inspect drafts; native permissions determine editing. */
export function WorkflowStudio({ recordId, active = true }: { recordId: string; active?: boolean }): React.ReactElement {
  const t = useWorkflowsT();
  const read = useAuthoredQuery(WorkflowStudioDocument, { id: recordId, target: recordId }, { models: [MODEL], enabled: active });
  if (!read.data) return read.error
    ? <ErrorBanner description={errorMessage(read.error, t("studio.loadFailed"))}
        actions={<Button onClick={() => void read.refetch()}>{t("studio.reload")}</Button>} />
    : <LoadingPanel message={t("studio.loading")} />;
  const record = read.data.workflow_by_pk;
  if (record?.draft == null || record.draft_revision == null) return <ErrorBanner description={t("studio.unavailable")} />;
  return <StudioSession key={recordId} recordId={recordId} initial={read.data}
    initialValues={studioValues(record.draft, record.layout)} initialRevision={record.draft_revision}
    writable={record.permissions.includes("write")} active={active} loadLatest={async () => {
      const latest = await read.refetch();
      if (latest.error) throw latest.error;
      if (!latest.data) throw new Error(t("studio.loadFailed"));
      return latest.data;
    }} />;
}

function StudioSession({ recordId, initial, initialValues, initialRevision, writable, loadLatest, active }: {
  recordId: string; initial: StudioRead; initialValues: StudioValues; initialRevision: number; writable: boolean;
  loadLatest: () => Promise<StudioRead>; active: boolean;
}): React.ReactElement {
  const t = useWorkflowsT();
  const preview = useRuntimeViewAs();
  const chatter = useChatter();
  const { widgets } = useAppRuntime();
  const configForm = React.useMemo(() => {
    const cache = new Map<string, { fields: readonly FormSpecFieldDescriptor[]; error?: string }>();
    return (step: string) => {
      let result = cache.get(step);
      if (!result) {
        try { result = { fields: deserializeFormSpec(initial.workflow_step_choices.find((choice) => choice.key === step)?.config_schema ?? emptyConfigSchema, widgets) }; }
        catch (cause) { result = { fields: [], error: errorMessage(cause, t("studio.configFailed")) }; }
        cache.set(step, result);
      }
      return result;
    };
  }, [initial.workflow_step_choices, widgets, t]);
  const configFields = React.useCallback((step: string) => configForm(step).fields, [configForm]);
  const displayLayout = React.useRef<GraphEditorLayout | null>(null);
  const layoutResolved = React.useCallback((layout: GraphEditorLayout) => { displayLayout.current = layout; }, []);
  const [selection, setSelection] = React.useState(EMPTY_SELECTION);
  const [intent, setIntent] = React.useState<AddIntent | null>(null);
  const [version, setVersion] = React.useState(initial.workflow_by_pk?.published?.number);
  const [dependents, setDependents] = React.useState<readonly string[]>([]);
  const [loadingLatest, setLoadingLatest] = React.useState(false);
  const revision = React.useRef(initialRevision);
  const [conflict, setConflict] = React.useState<{ message: string; revision: number | null } | null>(null);
  const [issues, setIssues] = React.useState<StudioIssues>(EMPTY_ISSUES);
  const [configurations, setConfigurations] = React.useState(() => configurationsFor(initialValues));
  const savedKeys = React.useRef(new Map(initialValues.entries.map((entry) => [entry.key, entry.clientId])));
  const usedKeys = React.useRef(new Set(initialValues.entries.map((entry) => entry.key)));
  const usedIds = React.useRef(new Set(initialValues.entries.map((entry) => entry.clientId)));
  const [save] = useAuthoredMutation(SaveWorkflowDraftDocument, { invalidateModels: [MODEL] });
  const [publish, publication] = useAuthoredMutation(PublishWorkflowDocument, { invalidateModels: [MODEL, "workflows.WorkflowVersion"] });
  const capture = (errors: ValidationErrors, keys: ReadonlyMap<string, string>, origin: IssueOrigin) => setIssues(captureStudioIssues(errors, keys, origin));
  function refusal(cause: unknown, keys: ReadonlyMap<string, string>, fallback: string, origin: IssueOrigin) {
    const result = formSubmitError(cause, fallback);
    if (result.status === "conflict") {
      const current = publicGraphQLErrorsFromUnknown(cause).find((error) => error.extensions.code === "STALE_REVISION")?.extensions.current_revision;
      setConflict({ message: result.message, revision: typeof current === "number" ? current : null });
    } else capture(result.issues, keys, origin);
    return result;
  }
  const action = useActionForm<StudioValues, Acknowledgement>({
    defaultValues: initialValues,
    fieldNames: [], genericErrorMessage: t("studio.saveFailed"),
    submit: async (values) => {
      setIssues(EMPTY_ISSUES);
      const snapshot = studioSnapshot({ ...values, layout: displayLayout.current ?? values.layout });
      if (snapshot.status !== "ok") {
        if (snapshot.status === "invalid") capture(snapshot.issues, new Map(values.entries.map((entry) => [entry.clientId, entry.clientId])), "local");
        return snapshot.status === "invalid" ? { ...snapshot, issues: { fieldErrors: {}, formErrors: [] } } : snapshot;
      }
      try {
        const response = await save({ id: recordId, draft: snapshot.data.draft, layout: snapshot.data.layout,
          nodeKeys: snapshot.data.nodeKeys, revision: revision.current });
        savedKeys.current = new Map(snapshot.data.clientIdByKey);
        const result = savedFormSubmitResult(response?.save_workflow_draft, t("studio.saveFailed"));
        if (result.status === "invalid") capture(result.issues, snapshot.data.clientIdByKey, "save");
        return result.status === "invalid" ? { ...result, issues: { fieldErrors: {}, formErrors: [] } } : result;
      } catch (cause) {
        const result = refusal(cause, snapshot.data.clientIdByKey, t("studio.saveFailed"), "save");
        // Stable issues are applied below at current paths, once, by applyFormErrors.
        return result.status === "invalid" ? { ...result, issues: { fieldErrors: {}, formErrors: [] } } : result;
      }
    },
    onSuccess: (values, acknowledgement) => {
      revision.current = acknowledgement.revision;
      setConflict(null);
      action.form.reset({ ...values, layout: displayLayout.current ?? values.layout });
      history.reset();
      capture(diagnosticErrors(acknowledgement.diagnostics), savedKeys.current, "saved");
    },
  });
  const form = action.form;
  const readOnly = !writable || Boolean(preview.viewAs || preview.pending);
  const disabled = readOnly || !active || action.submitting || publication.fetching || loadingLatest;
  const isDirtyNow = React.useCallback(() => form.formState.isDirty, [form]);
  useUnsavedChangesNavigationGuard({ isDirty: form.formState.isDirty, isDirtyNow, readOnly, allowSearchChanges: true });
  const history = useFormHistory(form, { readOnly: disabled });
  const topology = useWatch({ control: form.control, compute: (values: StudioValues) => JSON.stringify(values.entries.map(({ clientId, key, value }) => ({
    clientId, key, step: value.step, label: value.label, next: value.next,
  }))) });
  const entryIds = useWatch({ control: form.control, compute: (values: StudioValues) => values.entries.map((entry) => entry.clientId) });
  const projectedPaths = React.useRef<string[]>([]);
  const applyIssues = React.useCallback(() => {
    if (projectedPaths.current.length) form.clearErrors(projectedPaths.current as Parameters<typeof form.clearErrors>[0]);
    const entries = form.getValues().entries;
    const projected = projectStudioIssues(issues, entries, configFields);
    projectedPaths.current = Object.keys(projected.fieldErrors);
    applyFormErrors(form, { status: "invalid", issues: { fieldErrors: projected.fieldErrors, formErrors: [] } });
  }, [issues, configFields, form]);
  React.useEffect(() => { applyIssues(); }, [applyIssues, entryIds]);
  React.useEffect(() => {
    const subscription = form.watch((_values, { name, type }) => {
      if (type !== "change" || !name) return;
      const match = /^entries\.(\d+)\.(key|value\.(.+))$/.exec(name);
      if (!match) return;
      const entry = form.getValues().entries[Number(match[1])];
      if (entry) setIssues((current) => dropStudioIssue(current, entry.clientId, match[2] === "key" ? "[key]" : match[3]!));
    });
    return () => subscription.unsubscribe();
  }, [form]);
  const commitConfigurations = React.useCallback(() => {
    const values = form.getValues();
    for (const entry of values.entries) usedKeys.current.add(entry.key);
    const next = configurationsFor(values);
    setConfigurations((current) => stableSerialize(current) === stableSerialize(next) ? current : next);
  }, [form]);
  const index = entryIds.indexOf(selection.nodes[0] ?? "");
  const select = React.useCallback((next: GraphEditorSelection) => {
    setSelection(next);
    if (next.nodes.length) { chatter.setCollapsed(false); chatter.setActiveTab(INSPECTOR_TAB); }
  }, [chatter.setCollapsed, chatter.setActiveTab]);
  const update = React.useCallback((change: (values: StudioValues) => void) => {
    history.perform(() => {
      const values = structuredClone(form.getValues());
      for (const entry of values.entries) usedKeys.current.add(entry.key);
      change(values);
      form.setValue("entries", values.entries, { shouldDirty: true });
      form.setValue("layout", values.layout, { shouldDirty: true });
      form.setValue("document", values.document, { shouldDirty: true });
    });
    commitConfigurations();
  }, [form, history.perform, commitConfigurations]);
  const add = React.useCallback((choice: Choice) => {
    const origin = intent;
    update((values) => {
      let key = choice.key;
      for (let number = 2; usedKeys.current.has(key); number++) key = `${choice.key}_${number}`;
      usedKeys.current.add(key);
      const value = { step: choice.key, label: choice.label, config: v.parse(ConfigDefaults, choice.defaults).config, next: {} };
      let entry = createKeyedEntry<StudioNode>(value, key);
      while (usedIds.current.has(entry.clientId)) entry = createKeyedEntry<StudioNode>(value, key);
      usedIds.current.add(entry.clientId);
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
          const port = Object.keys(v.parse(OutcomesProjection, choice.outcomes)).find((name) => name !== "error");
          if (port) entry.value.next[port] = [origin.link.to];
        }
      }
      select({ nodes: [entry.clientId], link: null });
    });
    setIntent(null);
  }, [intent, update, select]);
  const palette = React.useMemo(() => <RailPanel title={t("studio.palette")}>
    <div className="grid gap-2 p-3">{initial.workflow_step_choices.filter((choice) => !choice.internal).map((choice) =>
      <Button key={choice.key} type="button" disabled={disabled} variant="ghost" onClick={() => add(choice)}>{choice.label}</Button>)}</div>
  </RailPanel>, [initial.workflow_step_choices, disabled, add, t]);
  const commit = React.useCallback((group: string) => { history.commit(group); commitConfigurations(); }, [history.commit, commitConfigurations]);
  const inspector = React.useMemo(() => ({ tabs: [{ id: INSPECTOR_TAB, label: t("studio.inspector"), icon: "settings",
    children: <StudioInspector index={index} form={form} configForm={configForm}
      disabled={disabled} start={history.start} commit={commit} />,
  }] }), [form, index, configForm, disabled, history.start, commit, t]);
  useChatterContent(active ? inspector : null);

  async function discard(): Promise<void> {
    setLoadingLatest(true);
    try {
      const latest = await loadLatest();
      const record = latest.workflow_by_pk;
      if (record?.draft == null || record.draft_revision == null) throw new Error(t("studio.unavailable"));
      const values = studioValues(record.draft, record.layout);
      form.reset(values);
      for (const entry of values.entries) { usedKeys.current.add(entry.key); usedIds.current.add(entry.clientId); }
      revision.current = record.draft_revision; setConflict(null); setIssues(EMPTY_ISSUES);
      savedKeys.current = new Map(values.entries.map((entry) => [entry.key, entry.clientId]));
      setVersion(record.published?.number); history.reset(); commitConfigurations();
    } catch (cause) { form.setError("root.server", { type: "server", message: errorMessage(cause, t("studio.loadFailed")) }); }
    finally { setLoadingLatest(false); }
  }
  async function publishSaved(): Promise<void> {
    if (disabled || form.formState.isDirty || conflict) return;
    setIssues(EMPTY_ISSUES); form.clearErrors("root.server");
    try {
      const response = await publish({ id: recordId, revision: revision.current });
      if (!response) throw new Error(t("studio.publishFailed"));
      setVersion(response.publish_workflow.number); setDependents(response.publish_workflow.dependents);
    } catch (cause) { refusal(cause, savedKeys.current, t("studio.publishFailed"), "publish"); }
  }
  const projected = projectStudioIssues(issues, form.getValues().entries, configFields);
  const issueIds = Object.keys(issues.nodes);
  const hasIssues = Object.keys(projected.fieldErrors).length || projected.formErrors.length;
  const issueMessage = issues.origin === "saved" ? t("studio.savedWithIssues") : issues.origin === "publish"
    ? t("studio.publishRefused") : issues.origin === "local" ? t("studio.duplicateKeys") : t("studio.saveRefused");
  return <ActionFormProvider {...form}>
    <PrimaryPanePublisher node={active ? palette : null} />
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 p-3">
        <Button type="button" disabled={disabled || !history.canUndo} onClick={() => { history.undo(); applyIssues(); commitConfigurations(); }}>{t("studio.undo")}</Button>
        <Button type="button" disabled={disabled || !history.canRedo} onClick={() => { history.redo(); applyIssues(); commitConfigurations(); }}>{t("studio.redo")}</Button>
        <Button type="button" disabled={disabled} onClick={() => setIntent({})}>{t("studio.addStep")}</Button>
        <Button type="button" disabled={disabled || !form.formState.isDirty} onClick={() => void action.run()}>{t("studio.save")}</Button>
        <Button type="button" disabled={disabled || form.formState.isDirty || conflict !== null} onClick={() => void publishSaved()}>{t("studio.publish")}</Button>
        <span className="text-sm text-fg-muted">{version == null ? t("studio.unpublished") : t("studio.version", { number: version })}</span>
      </div>
      {action.formError && (!conflict || !action.saveConflict) ? <ErrorBanner description={action.formError} /> : null}
      {hasIssues ? <ErrorBanner description={[issueMessage, ...projected.formErrors].join("\n")} /> : null}
      {conflict ? <Alert tone="warning"><span>{conflict.message}</span><div className="flex flex-wrap gap-2">
        <Button type="button" disabled={disabled || conflict.revision == null} onClick={() => {
          if (conflict.revision == null) return;
          revision.current = conflict.revision;
          void action.run();
        }}>{t("studio.overwrite")}</Button>
        <Button type="button" disabled={disabled} onClick={() => void discard()}>{t("studio.discard")}</Button>
      </div></Alert> : null}
      {dependents.length ? <Alert tone="info">{t("studio.dependents", { names: dependents.join(", ") })}</Alert> : null}
      <StudioGraph form={form} recordId={recordId} topology={topology} selection={selection} configurations={configurations}
        select={select} active={active} disabled={disabled} update={update} add={setIntent} issueIds={issueIds} layoutResolved={layoutResolved} />
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

type ConfigForm = (step: string) => { fields: readonly FormSpecFieldDescriptor[]; error?: string };
function StudioInspector({ index, form, configForm, disabled, start, commit }: {
  index: number; form: Form; configForm: ConfigForm; disabled: boolean;
  start: (group: string) => void; commit: (group: string) => void;
}): React.ReactElement {
  const state = useFormState({ control: form.control });
  const t = useWorkflowsT();
  return <ActionFormProvider {...form} formState={state}><PageAside collapse="never" gutter="compact" className="h-full w-full border-l-0">
    <RailPanel title={t("studio.inspector")} empty={t("studio.selectNode")}>
      {index < 0 ? null : <NodeInspector index={index} form={form} configForm={configForm} disabled={disabled} start={start} commit={commit} />}
    </RailPanel>
  </PageAside></ActionFormProvider>;
}

function NodeInspector({ index, form, configForm, disabled, start, commit }: {
  index: number; form: Form; configForm: ConfigForm; disabled: boolean;
  start: (group: string) => void; commit: (group: string) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const entry = useWatch({ control: form.control, compute: (values: StudioValues) => values.entries[index] });
  const config = configForm(entry?.value.step ?? "");
  const prefix = `entries.${index}`;
  const fields: readonly FormSpecFieldDescriptor[] = [
    { name: `${prefix}.key`, label: t("studio.key"), widget: "text" },
    { name: `${prefix}.value.label`, label: t("studio.label"), widget: "text" },
    { name: `${prefix}.value.config`, label: t("studio.config"), widget: "object", objectTemplate: config.fields },
  ];
  return <div className="grid gap-4 p-3" onFocusCapture={() => start(prefix)}
    onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) commit(prefix); }}>
    <DescriptorFieldList fields={fields} readOnly={disabled} />
    {config.error ? <ErrorBanner description={config.error} /> : null}
  </div>;
}

const StructureProjection = v.array(v.object({
  clientId: v.string(), key: v.string(), step: v.string(), label: v.string(),
  next: v.record(v.string(), v.union([v.string(), v.array(v.string())])),
}));
function StudioGraph({ form, recordId, topology, selection, select, configurations, active, disabled, update, add, issueIds, layoutResolved }: {
  form: Form; recordId: string; topology: string; selection: GraphEditorSelection;
  select: (value: GraphEditorSelection) => void; disabled: boolean; active: boolean;
  configurations: ReturnType<typeof configurationsFor>; issueIds: readonly string[];
  layoutResolved: (layout: GraphEditorLayout) => void;
  update: (change: (values: StudioValues) => void) => void; add: (intent: AddIntent) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const outcomes = useAuthoredQuery(WorkflowStepOutcomesDocument, { id: recordId, configurations }, {
    models: [MODEL], enabled: active, keepPreviousData: true,
  });
  const layout = useWatch({ control: form.control, compute: (values: StudioValues) => values.layout });
  const graph = React.useMemo(() => {
    const entries = v.parse(StructureProjection, JSON.parse(topology));
    const offered = new Map((outcomes.data?.workflow_step_outcomes ?? []).map((entry) => [entry.node, v.parse(OutcomesProjection, entry.outcomes)]));
    const ids = new Set(entries.map((entry) => entry.clientId));
    return {
      nodes: entries.map((entry) => ({ id: entry.clientId, kind: entry.step, title: `${entry.label || entry.step} (${entry.key})`,
        code: entry.key, ports: Object.entries(offered.get(entry.clientId) ?? {}).map(([id, label]) => ({ id, label })) })),
      links: entries.flatMap((entry) => Object.entries(entry.next).flatMap(([port, targets]) =>
        Object.hasOwn(offered.get(entry.clientId) ?? {}, port) ? (typeof targets === "string" ? [targets] : targets)
          .filter((to) => ids.has(to)).map((to) => ({ from: entry.clientId, port, to })) : [])),
    };
  }, [topology, outcomes.data]);
  const outcomeIssues = (outcomes.data?.workflow_step_outcomes ?? []).flatMap((node) => node.issues.map((issue) => ({ node: node.node, message: issue.message })));
  const status = Object.fromEntries([...issueIds, ...outcomeIssues.map((issue) => issue.node)].map((id) => [id, { label: t("studio.issues"), tone: "danger" as const }]));
  const canLink = React.useCallback((from: string, _port: string, to: string) => {
    const visited = new Set<string>();
    const reaches = (id: string): boolean => id === from || (!visited.has(id) && (visited.add(id), graph.links.some((edge) => edge.from === id && reaches(edge.to))));
    return !reaches(to);
  }, [graph.links]);
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
  return <div className="flex h-[65vh] min-h-[32rem] shrink-0 flex-col" data-testid="workflow-studio-canvas">
    {outcomes.error ? <ErrorBanner description={errorMessage(outcomes.error, t("studio.outcomesFailed"))} /> : null}
    {outcomeIssues.length ? <ErrorBanner description={outcomeIssues.map((issue) => `${form.getValues().entries.find((entry) => entry.clientId === issue.node)?.key ?? issue.node}: ${issue.message}`).join("\n")} /> : null}
    <GraphEditor {...graph} layoutOptions={{ rankdir: "LR" }} layout={layout} selected={selection} onSelectionChange={select} readOnly={disabled || !outcomes.data}
      className="min-h-0 flex-1" status={status} canLink={canLink} onLink={(value) => link(value)} onUnlink={(value) => link(value, true)}
      onLayoutResolved={layoutResolved}
      onLayoutChange={(value) => update((values) => { values.layout = value; })}
      onDelete={(ids) => update((values) => {
        values.entries = values.entries.filter((entry) => !ids.includes(entry.clientId));
        for (const entry of values.entries) for (const [port, targets] of Object.entries(entry.value.next)) {
          const next = (typeof targets === "string" ? [targets] : targets).filter((id) => !ids.includes(id));
          if (next.length) entry.value.next[port] = next;
          else delete entry.value.next[port];
        }
        values.layout = Object.fromEntries(Object.entries(values.layout).filter(([id]) => !ids.includes(id)));
      })} onAddFromPort={(from, port, position) => add({ from, port, position })}
      onInsertOnLink={(value, position) => add({ from: value.from, port: value.port, link: value, position })} />
  </div>;
}
