import * as v from "valibot";
import {
  JsonValueSchema, formSpecHasControlForPath, keyedCollectionFromRecord, keyedCollectionToRecord,
  type JsonValue, type KeyedCollection, type GraphEditorLayout, type FormSubmitResult, type ValidationErrors,
  type FormSpecFieldDescriptor,
} from "@angee/ui";

// Unknown declarations survive edits; Definition owns document and binding validation.
const NodeProjection = v.looseObject({
  step: v.string(), label: v.optional(v.string(), ""),
  config: v.optional(v.record(v.string(), v.unknown()), {}),
  next: v.optional(v.record(v.string(), v.union([v.string(), v.array(v.string())])), {}),
});
const DocumentProjection = v.looseObject({ nodes: v.record(v.string(), NodeProjection) });
const LayoutProjection = v.record(v.string(), v.tuple([v.number(), v.number()]));
export const OutcomesProjection = v.record(v.string(), v.string());
const DiagnosticsProjection = v.array(v.object({
  node: v.nullable(v.string()), path: v.array(v.union([v.string(), v.number()])), code: v.string(), message: v.string(),
}));
export type StudioNode = v.InferOutput<typeof NodeProjection>;
export type StudioValues = {
  entries: KeyedCollection<StudioNode>;
  layout: GraphEditorLayout;
  document: Record<string, unknown>;
};
export type IssueOrigin = "saved" | "save" | "publish" | "local";
export type StudioIssues = { nodes: Record<string, Record<string, readonly string[]>>; formErrors: readonly string[]; origin?: IssueOrigin };
export const EMPTY_ISSUES: StudioIssues = { nodes: {}, formErrors: [] };

/** Loaded authored keys also serve as the session's initial client identities. */
export function studioValues(draft: unknown, layout: unknown): StudioValues {
  const { nodes, ...document } = v.parse(DocumentProjection, v.parse(JsonValueSchema, draft));
  return { entries: keyedCollectionFromRecord(nodes), document,
    layout: Object.fromEntries(Object.entries(v.parse(LayoutProjection, layout ?? {}))
      .map(([id, [x, y]]) => [id, { x, y }])) };
}

/** Send client-addressed declarations unchanged; Definition performs every re-key. */
export function studioSnapshot(values: StudioValues): FormSubmitResult<{
  draft: JsonValue; layout: Record<string, readonly [number, number]>;
  nodeKeys: Record<string, string>; clientIdByKey: ReadonlyMap<string, string>;
}> {
  const snapshot = keyedCollectionToRecord(values.entries);
  if (snapshot.status !== "ok") return snapshot;
  return { status: "ok", data: {
    draft: v.parse(JsonValueSchema, { ...values.document, nodes: Object.fromEntries(values.entries.map((entry) => [entry.clientId, entry.value])) }),
    layout: Object.fromEntries(Object.entries(values.layout).map(([id, point]) => [id, [point.x, point.y] as const])),
    nodeKeys: Object.fromEntries(snapshot.data.keyByClientId), clientIdByKey: snapshot.data.clientIdByKey,
  } };
}

/** Capture server paths against the submission's keys, independently of array order. */
export function captureStudioIssues(issues: ValidationErrors, ids: ReadonlyMap<string, string>, origin: IssueOrigin = "save"): StudioIssues {
  const nodes: StudioIssues["nodes"] = {};
  const formErrors = [...issues.formErrors];
  for (const [path, messages] of Object.entries(issues.fieldErrors)) {
    const key = path.startsWith("nodes.")
      ? [...ids.keys()].sort((a, b) => b.length - a.length).find((key) => path === `nodes.${key}` || path.startsWith(`nodes.${key}.`))
      : path;
    const id = key === undefined ? undefined : ids.get(key);
    if (id === undefined) { formErrors.push(...messages); continue; }
    const suffix = key !== undefined && path.startsWith(`nodes.${key}.`) ? path.slice(`nodes.${key}.`.length) : "[key]";
    const fields = nodes[id] ??= {};
    fields[suffix] = [...(fields[suffix] ?? []), ...messages];
  }
  return { nodes, formErrors, origin };
}

/** Forget refusals only for the edited control, retaining other nodes and form failures. */
export function dropStudioIssue(issues: StudioIssues, id: string, path: string): StudioIssues {
  const fields = issues.nodes[id];
  if (!fields) return issues;
  const retained = Object.fromEntries(Object.entries(fields).filter(([issuePath]) =>
    !(issuePath === path || issuePath.startsWith(`${path}.`) || path.startsWith(`${issuePath}.`))));
  if (Object.keys(retained).length === Object.keys(fields).length) return issues;
  const nodes = { ...issues.nodes };
  if (Object.keys(retained).length) nodes[id] = retained;
  else delete nodes[id];
  return { ...issues, nodes };
}

/** Re-project stable issues to today's form paths; unrendered declarations stay visible. */
export function projectStudioIssues(issues: StudioIssues, entries: StudioValues["entries"], configFields: (step: string) => readonly FormSpecFieldDescriptor[]): ValidationErrors {
  const fieldErrors: ValidationErrors["fieldErrors"] = {};
  const formErrors = [...issues.formErrors];
  for (const [id, fields] of Object.entries(issues.nodes)) {
    const index = entries.findIndex((entry) => entry.clientId === id);
    const entry = entries[index];
    if (!entry) continue;
    for (const [path, messages] of Object.entries(fields)) {
      const suffix = path === "[key]" ? "key" : `value.${path}`;
      if (path === "[key]" || path === "label" || path === "config"
          || (path.startsWith("config.") && formSpecHasControlForPath(configFields(entry.value.step), path.slice("config.".length), entry.value.config))) {
        fieldErrors[`entries.${index}.${suffix}`] = messages;
      } else formErrors.push(...messages.map((message) => `${entry.key}: ${message}`));
    }
  }
  return { fieldErrors, formErrors };
}

export function diagnosticErrors(diagnostics: unknown): ValidationErrors {
  const fieldErrors: Record<string, string[]> = {};
  const formErrors: string[] = [];
  for (const issue of v.parse(DiagnosticsProjection, diagnostics)) {
    if (issue.path.length) (fieldErrors[issue.path.join(".")] ??= []).push(issue.message);
    else formErrors.push(issue.message);
  }
  return { fieldErrors, formErrors };
}
