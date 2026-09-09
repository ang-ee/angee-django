import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button, Checkbox, Collapsible, DialogBackdrop, DialogContent, DialogDescription, DialogHeader,
  DialogPortal, DialogRoot, DialogTitle, ErrorBanner, FieldDescriptorControl,
  FieldLabel, FieldRoot, Input, TreeView,
  useListIdentities,
  type FieldDescriptor, type WidgetFocusTarget,
} from "@angee/ui";

import { WorkflowInputSourcesDocument, WorkflowStepOperationsDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { useWorkflowInputPreview, type WorkflowInputPreviewRequest } from "./workflow-input-preview";

type InputSourcesPayload = NonNullable<DocumentType<typeof WorkflowInputSourcesDocument>["workflow_input_sources"]>;
type InputSource = InputSourcesPayload["sources"][number];
type StepOperation = DocumentType<typeof WorkflowStepOperationsDocument>["workflow_step_operations"][number];

export type BindingPath = readonly (string | number)[];
export type InputBinding = Record<string, unknown> | null;
export type DataContract = StepOperation["input_contract"];
export type ContractNode = DataContract["nodes"][number];
export type ContractEdge = DataContract["edges"][number];
export type OperationContract = Pick<StepOperation, "key" | "input_contract">;

interface Props {
  nodeKey: string;
  operation?: OperationContract;
  value: InputBinding;
  readOnly: boolean;
  messages: readonly string[];
  detailPath?: BindingPath;
  controlRef?: (target: WidgetFocusTarget | null) => void;
  onChange: (value: InputBinding) => void;
  onCommit: () => void;
  onStructuralChange: (value: InputBinding) => void;
  onFocused: () => void;
  onGoToSource?: (identity: string) => void;
  sourceLabel?: (identity: string) => string | undefined;
}

export function WorkflowInputBindingEditor(props: Props): React.ReactElement {
  const t = useWorkflowsT();
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [pickerPath, setPickerPath] = React.useState<BindingPath>([]);
  const inputContract = props.operation?.input_contract;
  const root = contractNode(inputContract, inputContract?.root_node_id);
  const kind = typeof props.value?.kind === "string" ? props.value.kind : null;
  const focusRefs = React.useRef(new Map<string, WidgetFocusTarget>());
  const pendingFocus = React.useRef<string | null>(null);
  const focusModeOnRender = React.useRef(false);
  React.useLayoutEffect(() => {
    if (props.value != null && focusModeOnRender.current) {
      focusModeOnRender.current = false;
      focusRefs.current.get(pathKey(["kind"]))?.focus();
    }
  }, [props.value]);
  React.useLayoutEffect(() => {
    if (!pendingFocus.current) return;
    const target = focusRefs.current.get(pendingFocus.current);
    if (target) {
      pendingFocus.current = null;
      target.focus();
    }
  }, [props.value]);
  React.useEffect(() => {
    if (!props.detailPath) return;
    const target = focusTarget(focusRefs.current, props.detailPath);
    if (target) { target.focus(); props.onFocused(); }
  }, [props.detailPath, props.onFocused]);
  React.useEffect(() => {
    props.controlRef?.({ focus: () => (focusTarget(focusRefs.current, props.detailPath ?? []) ?? focusRefs.current.values().next().value)?.focus() });
    return () => props.controlRef?.(null);
  }, [props.controlRef, props.detailPath]);
  const controlRef = (path: BindingPath) => (target: WidgetFocusTarget | null) => {
    const element = target;
    if (element) focusRefs.current.set(pathKey(path), element);
    else focusRefs.current.delete(pathKey(path));
  };
  const requestFocus = (path: BindingPath) => { pendingFocus.current = pathKey(path); };
  return <div className="grid gap-3">
    {props.messages.length ? <ErrorBanner description={props.messages.join(" ")} /> : null}
    {props.value == null ? <div className="grid gap-3 rounded-6 border border-border-subtle p-3">
      <strong>{t("input.automatic")}</strong>
      <p className="text-13 text-fg-muted">{t("input.automaticDescription")}</p>
      {!props.readOnly ? <Button type="button" size="sm" onClick={() => { focusModeOnRender.current = true; props.onStructuralChange({}); }}>{t("input.map")}</Button> : null}
    </div> : <>
      <div className="flex flex-wrap gap-2">
        {(["constant", "reference", "object", "array"] as const).map((mode, index) => <Button key={mode} ref={index === 0 ? controlRef(["kind"]) : undefined} type="button" size="sm" variant={kind === mode || (mode === "reference" && isReferenceKind(kind)) ? "primary" : "secondary"} disabled={props.readOnly} onClick={() => mode === "reference" ? (setPickerPath([]), setPickerOpen(true)) : props.onStructuralChange(bindingForMode(mode))}>{modeLabel(mode, t)}</Button>)}
      </div>
      {kind === "constant" ? <ConstantEditor binding={props.value} contract={root} readOnly={props.readOnly} controlRef={controlRef(["value"])} onChange={props.onChange} onCommit={props.onCommit} onStructuralChange={props.onStructuralChange} /> : null}
      {kind === "object" ? <ObjectEditor binding={props.value} contract={props.operation?.input_contract} node={root} readOnly={props.readOnly} path={[]} controlRef={controlRef} requestFocus={requestFocus} onChange={props.onChange} onCommit={props.onCommit} onStructuralChange={props.onStructuralChange} onReference={(path) => { setPickerPath(path); setPickerOpen(true); }} onGoToSource={props.onGoToSource} sourceLabel={props.sourceLabel} /> : null}
      {kind === "array" ? <ArrayEditor binding={props.value} contract={props.operation?.input_contract} node={root} readOnly={props.readOnly} path={[]} controlRef={controlRef} requestFocus={requestFocus} onChange={props.onChange} onCommit={props.onCommit} onStructuralChange={props.onStructuralChange} onReference={(path) => { setPickerPath(path); setPickerOpen(true); }} onGoToSource={props.onGoToSource} sourceLabel={props.sourceLabel} /> : null}
      {isReferenceKind(kind) ? <ReferenceSummary binding={props.value} controlRef={controlRef([])} onChange={() => { setPickerPath([]); setPickerOpen(true); }} readOnly={props.readOnly} onGoToSource={props.onGoToSource} sourceLabel={props.sourceLabel} /> : null}
      {!props.readOnly ? <Button type="button" size="sm" variant="ghost" onClick={() => props.onStructuralChange(null)}>{t("input.automaticReturn")}</Button> : null}
    </>}
    <SourcePicker nodeKey={props.nodeKey} open={pickerOpen} onOpenChange={setPickerOpen} onUse={(reference) => {
      requestFocus(pickerPath);
      props.onStructuralChange(pickerPath.length ? replaceBinding(props.value, pickerPath, reference) : reference);
    }} />
  </div>;
}

function ConstantEditor({ binding, contract, readOnly, controlRef, onChange, onCommit, onStructuralChange }: { binding: Record<string, unknown>; contract?: ContractNode; readOnly: boolean; controlRef: (target: WidgetFocusTarget | null) => void; onChange: (value: InputBinding) => void; onCommit: () => void; onStructuralChange: (value: InputBinding) => void; }): React.ReactElement {
  const t = useWorkflowsT();
  const nullId = React.useId();
  const isNull = binding.value === null;
  const field = scalarField(contract, t("input.literal"));
  return <div className="grid gap-2">
    <FieldRoot>
      <div className="flex items-center gap-2">
        <Checkbox id={nullId} checked={isNull} disabled={readOnly} onCheckedChange={(checked) => {
        if (checked === true) onStructuralChange({ kind: "constant", value: null });
        else {
          const { value: _value, ...incomplete } = binding;
          onStructuralChange(incomplete);
        }
      }} />
        <FieldLabel htmlFor={nullId}>{t("input.null")}</FieldLabel>
      </div>
    </FieldRoot>
    {!isNull ? <FieldRoot>
      <FieldLabel>{field.label}</FieldLabel>
      <FieldDescriptorControl field={field} value={binding.value} readOnly={readOnly} controlRef={controlRef} onChange={(value) => onChange({ kind: "constant", value })} onCommit={onCommit} />
    </FieldRoot> : null}
  </div>;
}

function ObjectEditor({ binding, contract, node, readOnly, path, controlRef, requestFocus, onChange, onCommit, onStructuralChange, onReference, onGoToSource, sourceLabel }: StructuredProps): React.ReactElement {
  const t = useWorkflowsT();
  const fieldId = React.useId();
  const fieldListId = React.useId();
  const fields = objectFields(binding.fields);
  const [key, setKey] = React.useState("");
  const declared = contractEdges(contract, node?.id).filter((edge) => edge.kind === "field");
  return <div className="grid gap-3">
    {Object.entries(fields).map(([name, child]) => <section key={name} className="grid gap-2 rounded-6 border border-border-subtle p-2">
      <div className="flex items-center justify-between gap-2">
        <strong className="min-w-0 truncate text-13">{name}</strong>{!readOnly ? <Button type="button" size="sm" variant="ghost" onClick={() => { const next = { ...fields }; delete next[name]; onStructuralChange({ ...binding, fields: next }); }}>{t("input.remove")}</Button> : null}</div>
      <NestedBinding value={child} contract={contract} node={childContract(contract, node, "field", name)} readOnly={readOnly} path={[...path, "fields", name]} controlRef={controlRef} requestFocus={requestFocus} onChange={(next) => onChange({ ...binding, fields: { ...fields, [name]: next } })} onCommit={onCommit} onStructuralChange={(next) => onStructuralChange({ ...binding, fields: { ...fields, [name]: next } })} onReference={onReference} onGoToSource={onGoToSource} sourceLabel={sourceLabel} />
    </section>)}
    {!readOnly ? <FieldRoot>
      <FieldLabel htmlFor={fieldId}>{t("input.field")}</FieldLabel>
      <div className="flex gap-2">
        <Input
          id={fieldId}
          className="min-w-0 flex-1"
          list={fieldListId}
          value={key}
          ref={controlRef([...path, "fields"] as BindingPath)}
          onChange={(event) => setKey(event.currentTarget.value)}
        />
        <datalist id={fieldListId}>{declared.map((edge) => <option key={edge.key!} value={edge.key!} />)}</datalist>
        <Button type="button" size="sm" disabled={!key || Object.hasOwn(fields, key)} onClick={() => { requestFocus([...path, "fields", key, "kind"]); onStructuralChange({ ...binding, fields: { ...fields, [key]: {} } }); setKey(""); }}>{t("input.add")}</Button>
      </div>
    </FieldRoot> : null}
  </div>;
}

function ArrayEditor({ binding, contract, node, readOnly, path, controlRef, requestFocus, onChange, onCommit, onStructuralChange, onReference, onGoToSource, sourceLabel }: StructuredProps): React.ReactElement {
  const t = useWorkflowsT();
  const items = Array.isArray(binding.items) ? binding.items as InputBinding[] : [];
  const [identities, nextIdentity] = useListIdentities(items.length);
  return <div className="grid gap-2">{items.map((child, index) => <section key={identities.current[index]} className="grid gap-2 rounded-6 border border-border-subtle p-2">
    <div className="flex justify-between">
      <strong className="text-13">{t("input.item", { index: index + 1 })}</strong>{!readOnly ? <Button type="button" size="sm" variant="ghost" onClick={() => { identities.current.splice(index, 1); onStructuralChange({ ...binding, items: items.filter((_, candidate) => candidate !== index) }); }}>{t("input.remove")}</Button> : null}</div>
    <NestedBinding value={child} contract={contract} node={childContract(contract, node, "item")} readOnly={readOnly} path={[...path, "items", index]} controlRef={controlRef} requestFocus={requestFocus} onChange={(next) => { const copy = [...items]; copy[index] = next; onChange({ ...binding, items: copy }); }} onCommit={onCommit} onStructuralChange={(next) => { const copy = [...items]; copy[index] = next; onStructuralChange({ ...binding, items: copy }); }} onReference={onReference} onGoToSource={onGoToSource} sourceLabel={sourceLabel} />
  </section>)}{!readOnly ? <Button type="button" size="sm" variant="secondary" onClick={() => { identities.current.push(nextIdentity()); requestFocus([...path, "items", items.length, "kind"]); onStructuralChange({ ...binding, items: [...items, {}] }); }}>{t("input.addItem")}</Button> : null}</div>;
}

interface StructuredProps { binding: Record<string, unknown>; contract?: DataContract; node?: ContractNode; readOnly: boolean; path: BindingPath; controlRef: (path: BindingPath) => (target: WidgetFocusTarget | null) => void; requestFocus: (path: BindingPath) => void; onChange: (value: InputBinding) => void; onCommit: () => void; onStructuralChange: (value: InputBinding) => void; onReference: (path: BindingPath) => void; onGoToSource?: (identity: string) => void; sourceLabel?: (identity: string) => string | undefined; }
function NestedBinding(props: Omit<StructuredProps, "binding"> & { value: InputBinding; }): React.ReactElement {
  const t = useWorkflowsT();
  const kind = typeof props.value?.kind === "string" ? props.value.kind : null;
  const changeMode = (mode: "constant" | "object" | "array") => {
    props.requestFocus([...props.path, "kind"]);
    props.onStructuralChange(bindingForMode(mode));
  };
  if (!kind) return <ModeChoice readOnly={props.readOnly} controlRef={props.controlRef([...props.path, "kind"])} onChoose={changeMode} onReference={() => props.onReference(props.path)} />;
  const editor = kind === "constant"
    ? <ConstantEditor binding={props.value!} contract={props.node} readOnly={props.readOnly} controlRef={props.controlRef([...props.path, "value"])} onChange={props.onChange} onCommit={props.onCommit} onStructuralChange={props.onStructuralChange} />
    : kind === "object" ? <ObjectEditor {...props} binding={props.value!} />
      : kind === "array" ? <ArrayEditor {...props} binding={props.value!} />
        : isReferenceKind(kind) ? <ReferenceSummary binding={props.value!} readOnly={props.readOnly} controlRef={props.controlRef(props.path)} onChange={() => props.onReference(props.path)} onGoToSource={props.onGoToSource} sourceLabel={props.sourceLabel} />
          : null;
  if (!editor) return <ModeChoice readOnly={props.readOnly} controlRef={props.controlRef([...props.path, "kind"])} onChoose={changeMode} onReference={() => props.onReference(props.path)} />;
  return <div className="grid gap-2">
    {!props.readOnly ? <FieldDescriptorControl
      field={{ name: "kind", label: t("input.change"), widget: "select", options: (["constant", "reference", "object", "array"] as const).map((mode) => ({ value: mode, label: modeLabel(mode, t) })) }}
      value={isReferenceKind(kind) ? "reference" : kind}
      controlRef={props.controlRef([...props.path, "kind"])}
      onChange={(value) => {
        const mode = String(value ?? "");
        if (mode === "reference") props.onReference(props.path);
        else if (mode === "constant" || mode === "object" || mode === "array") changeMode(mode);
      }}
    /> : null}
    {editor}
  </div>;
}
function ModeChoice({ onChoose, onReference, readOnly, controlRef }: {
  onChoose: (mode: "constant" | "object" | "array") => void;
  onReference: () => void;
  readOnly: boolean;
  controlRef: (target: WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="flex flex-wrap gap-1">
    {(["constant", "object", "array"] as const).map((mode, index) => (
      <Button key={mode} ref={index === 0 ? controlRef : undefined} disabled={readOnly}
        type="button" size="sm" variant="secondary" onClick={() => onChoose(mode)}>
        {modeLabel(mode, t)}
      </Button>
    ))}
    <Button type="button" disabled={readOnly} size="sm" variant="secondary" onClick={onReference}>
      {t("input.mode.reference")}
    </Button>
  </div>;
}

function ReferenceSummary({ binding, readOnly, onChange, onGoToSource, sourceLabel, controlRef }: {
  binding: Record<string, unknown>;
  readOnly: boolean;
  onChange?: () => void;
  onGoToSource?: (identity: string) => void;
  sourceLabel?: (identity: string) => string | undefined;
  controlRef?: (target: WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const path = Array.isArray(binding.path) ? binding.path as BindingPath : [];
  const identity = String(binding.step_key ?? "");
  const resolvedSource = identity ? sourceLabel?.(identity) : undefined;
  const source = binding.kind === "step_output"
    ? resolvedSource || identity
    : binding.kind === "workflow_input" ? t("input.workflowSource") : t("input.mapSource");
  return <div className="grid gap-2 rounded-6 border border-border-subtle p-3">
    <strong>{source}</strong>
    <span className="text-13">{displayPath(path) || t("input.wholeValue")}</span>
    <div className="flex gap-2">
      {onChange && !readOnly ? <Button ref={controlRef} type="button" size="sm" variant="secondary" onClick={onChange}>{t("input.change")}</Button> : null}
      {resolvedSource && onGoToSource ? <Button type="button" size="sm" variant="ghost" onClick={() => onGoToSource(identity)}>{t("input.goToSource")}</Button> : null}
    </div>
    <Collapsible variant="section">
      <Collapsible.Trigger>{t("input.technical")}</Collapsible.Trigger>
      <Collapsible.Panel>
        <code className="break-all text-12">{JSON.stringify(binding)}</code>
      </Collapsible.Panel>
    </Collapsible>
  </div>;
}

interface SourceRow extends Record<string, unknown> { id: string; parentId: string; label: string; description?: string; unknown?: boolean; sourceIndex: number; nodeId: number; path: BindingPath; itemKey?: string; selectable: boolean; }
function SourcePicker({ nodeKey, open, onOpenChange, onUse }: { nodeKey: string; open: boolean; onOpenChange: (open: boolean) => void; onUse: (binding: InputBinding) => void; }): React.ReactElement {
  const t = useWorkflowsT(); const owner = useWorkflowInputPreview();
  const arrayIndexId = React.useId();
  const [request, setRequest] = React.useState<{ variables: WorkflowInputPreviewRequest; token: number; } | null>(null);
  const token = React.useRef(0);
  const [selectedId, setSelectedId] = React.useState<string | null>(null); const [indices, setIndices] = React.useState<Record<string, string>>({});
  const [admitted, setAdmitted] = React.useState<{ token: number; payload: InputSourcesPayload; } | null>(null);
  React.useEffect(() => { if (!open) return; const variables = owner?.prepare(nodeKey); const nextToken = ++token.current; setRequest(variables ? { variables, token: nextToken } : null); setAdmitted(null); setSelectedId(null); setIndices({}); }, [nodeKey, open, owner]);
  const query = useAuthoredQuery(WorkflowInputSourcesDocument, request?.variables, { enabled: open && request != null });
  const response = query.data?.workflow_input_sources;
  React.useEffect(() => { if (!request || query.isFetching || query.error || !response) return; if (response.status === "STALE") { if (request.token === token.current) owner?.stale(); return; } if (response.status === "SUCCESS" && request.token === token.current) setAdmitted({ token: request.token, payload: response }); }, [owner, query.error, query.isFetching, request, response]);
  const payload = admitted && request && admitted.token === request.token ? admitted.payload : null;
  const rows = React.useMemo(() => sourceRows(payload?.sources ?? [], indices), [indices, payload?.sources]);
  const selected = rows.find((row) => row.id === selectedId) ?? null;
  return <DialogRoot open={open} onOpenChange={onOpenChange}>
    <DialogPortal>
      <DialogBackdrop />
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{t("input.pickerTitle")}</DialogTitle>
          <DialogDescription>{t("input.pickerDescription")}</DialogDescription>
        </DialogHeader>{query.error ? <div className="grid gap-2">
          <ErrorBanner description={t("input.sourceError")} />
          <Button type="button" size="sm" variant="secondary" onClick={() => { void query.refetch(); }}>{t("input.retry")}</Button>
        </div> : null}{response?.status === "STRUCTURAL" ? <ErrorBanner description={response.diagnostics.map((item) => item.message).join(" ")} /> : null}<TreeView<SourceRow> rows={rows} selectedId={selectedId ?? undefined} onSelect={(row) => setSelectedId(row.id)} renderRow={(row) => <span>
          <span className="block">{row.label}</span>{row.itemKey ? <span className="block text-xs text-fg-muted">{t("input.arrayIndex")}</span> : row.unknown ? <span className="block text-xs text-fg-muted">{t("input.shapeUnknown")}</span> : row.description ? <span className="block text-xs text-fg-muted">{row.description}</span> : null}</span>} emptyContent={query.isFetching ? t("input.loadingSources") : t("input.noSources")} />{selected?.itemKey ? <FieldRoot>
          <FieldLabel htmlFor={arrayIndexId}>{t("input.arrayIndex")}</FieldLabel>
          <Input id={arrayIndexId} autoFocus inputMode="numeric"
            value={indices[selected.itemKey] ?? ""}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setIndices((current) => ({ ...current, [selected.itemKey!]: value }));
            }}
          /></FieldRoot> : null}<div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("input.cancel")}</Button>
          <Button type="button" disabled={query.isFetching || Boolean(query.error) || !payload || !selected?.selectable} onClick={() => { if (!selected || !payload) return; const source = payload.sources[selected.sourceIndex]; if (!source) return; onUse(referenceBinding(source, selected.path)); onOpenChange(false); }}>{t("input.useValue")}</Button>
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>;
}

function sourceRows(sources: readonly InputSource[], indices: Record<string, string>): SourceRow[] { const rows: SourceRow[] = []; sources.forEach((source, sourceIndex) => { const prefix = `s${sourceIndex}`; const root = contractNode(source.contract, source.contract.root_node_id); rows.push({ id: prefix, parentId: "", label: source.label || source.step_key || source.kind, unknown: root?.kind === "unknown", sourceIndex, nodeId: source.contract.root_node_id, path: [], selectable: true }); appendRows(rows, source.contract, source.contract.root_node_id, prefix, sourceIndex, [], indices); }); return rows; }
function appendRows(rows: SourceRow[], contract: DataContract, parentNode: number, parentId: string, sourceIndex: number, path: BindingPath, indices: Record<string, string>): void { for (const edge of contract.edges.filter((item) => item.parent_node_id === parentNode)) { const node = contractNode(contract, edge.child_node_id); if (!node) continue; if (edge.kind === "field") { const next = [...path, edge.key!] as BindingPath; const id = `${parentId}/f${edge.child_node_id}`; rows.push({ id, parentId, label: node.title || edge.key!, description: node.description ?? undefined, unknown: node.kind === "unknown", sourceIndex, nodeId: node.id, path: next, selectable: true }); appendRows(rows, contract, node.id, id, sourceIndex, next, indices); } else { const itemKey = `${sourceIndex}:${parentId}:${node.id}`; const raw = indices[itemKey]; const parsed = raw != null && /^\d+$/.test(raw) ? Number(raw) : Number.NaN; const valid = Number.isSafeInteger(parsed) && parsed >= 0; const next = [...path, valid ? parsed : 0] as BindingPath; const id = `${parentId}/i${node.id}`; rows.push({ id, parentId, label: valid ? `Item ${parsed + 1}` : "Array item", sourceIndex, nodeId: node.id, itemKey, path: next, selectable: valid }); if (valid) appendRows(rows, contract, node.id, id, sourceIndex, next, indices); } } }
function referenceBinding(source: InputSource, path: BindingPath): InputBinding { if (source.kind === "step_output") return { kind: "step_output", step_key: source.step_key, path: [...path] }; return { kind: source.kind, path: [...path] }; }
function contractNode(contract: DataContract | undefined, id: number | undefined): ContractNode | undefined { return contract?.nodes.find((node) => node.id === id); }
function contractEdges(contract: DataContract | undefined, id: number | undefined): readonly ContractEdge[] { return id == null ? [] : contract?.edges.filter((edge) => edge.parent_node_id === id) ?? []; }
function childContract(contract: DataContract | undefined, node: ContractNode | undefined, kind: string, key?: string): ContractNode | undefined { const edge = contractEdges(contract, node?.id).find((candidate) => candidate.kind === kind && (kind !== "field" || candidate.key === key)); return contractNode(contract, edge?.child_node_id); }
function objectFields(value: unknown): Record<string, InputBinding> { return value != null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, InputBinding> : {}; }
function bindingForMode(mode: string): InputBinding { if (mode === "constant") return { kind: "constant" }; if (mode === "object") return { kind: "object", fields: {} }; if (mode === "array") return { kind: "array", items: [] }; return {}; }
function scalarField(node: ContractNode | undefined, label: string): FieldDescriptor { const widget = node?.kind !== "scalar" ? "json" : node.json_type === "boolean" ? "boolean" : ["integer", "number"].includes(node.json_type ?? "") ? "number" : node.json_type === "string" ? "text" : "json"; return { name: "value", label: node?.title || label, description: node?.description || undefined, widget }; }
function replaceBinding(root: InputBinding, path: BindingPath, replacement: InputBinding): InputBinding {
  if (path.length === 0) return replacement;
  const [container, key, ...rest] = path;
  if (container === "fields" && typeof key === "string" && root?.kind === "object") {
    const fields = objectFields(root.fields);
    return { ...root, fields: { ...fields, [key]: replaceBinding(fields[key] ?? null, rest, replacement) } };
  }
  if (container === "items" && typeof key === "number" && root?.kind === "array") {
    const items = Array.isArray(root.items) ? [...root.items] as InputBinding[] : [];
    items[key] = replaceBinding(items[key] ?? null, rest, replacement);
    return { ...root, items };
  }
  return root;
}
function isReferenceKind(kind: string | null): boolean { return kind === "workflow_input" || kind === "step_output" || kind === "map_item"; }
function pathKey(path: BindingPath): string { return JSON.stringify(path); }
function focusTarget(refs: Map<string, { focus: () => void; }>, path: BindingPath): { focus: () => void; } | undefined {
  const exact = refs.get(pathKey(path));
  if (exact) return exact;
  if (path.at(-1) === "path") {
    const referencePath = path.slice(0, -1);
    const reference = refs.get(pathKey(referencePath));
    if (reference) return reference;
    const referenceKind = refs.get(pathKey([...referencePath, "kind"]));
    if (referenceKind) return referenceKind;
  }
  const child = refs.get(pathKey([...path, "kind"]));
  if (child) return child;
  for (let length = path.length - 1;length >= 0;length -= 1) {
    const ancestor = refs.get(pathKey(path.slice(0, length)));
    if (ancestor) return ancestor;
  }
  return refs.get(pathKey(["kind"]));
}
function displayPath(path: BindingPath): string { return path.map((segment) => typeof segment === "number" ? `[${segment}]` : JSON.stringify(segment)).join(" → "); }

function modeLabel(mode: "constant" | "reference" | "object" | "array", t: ReturnType<typeof useWorkflowsT>): string {
  if (mode === "constant") return t("input.mode.constant");
  if (mode === "reference") return t("input.mode.reference");
  if (mode === "object") return t("input.mode.object");
  return t("input.mode.array");
}
