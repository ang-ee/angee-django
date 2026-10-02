import * as v from "valibot";
import {
  JsonValueSchema, keyedCollectionFromRecord, keyedCollectionToRecord,
  type JsonValue, type KeyedCollection, type GraphEditorLayout, type FormSubmitResult, type ValidationErrors,
} from "@angee/ui";

// This is the authoring projection, not a second definition validator. Unknown
// declaration fields survive every edit; Definition remains the schema owner.
const NodeProjection = v.looseObject({
  step: v.string(), label: v.optional(v.string(), ""),
  config: v.optional(v.record(v.string(), v.unknown()), {}),
  next: v.optional(v.record(v.string(), v.union([v.string(), v.array(v.string())])), {}),
});
const DocumentProjection = v.looseObject({ nodes: v.record(v.string(), NodeProjection) });
const LayoutProjection = v.record(v.string(), v.tuple([v.number(), v.number()]));
export const OutcomesProjection = v.record(v.string(), v.string());
export const DiagnosticsProjection = v.array(v.object({
  node: v.nullable(v.string()), path: v.array(v.union([v.string(), v.number()])), code: v.string(), message: v.string(),
}));
export type StudioNode = v.InferOutput<typeof NodeProjection>;
export type StudioValues = {
  entries: KeyedCollection<StudioNode>;
  layout: GraphEditorLayout;
  document: Record<string, unknown>;
};

/** Convert persisted references and layout keys to stable client identities. */
export function studioValues(draft: unknown, layout: unknown): StudioValues {
  const { nodes, ...document } = v.parse(DocumentProjection, v.parse(JsonValueSchema, draft));
  return { entries: keyedCollectionFromRecord(nodes), document,
    layout: Object.fromEntries(Object.entries(v.parse(LayoutProjection, layout ?? {}))
      .map(([id, [x, y]]) => [id, { x, y }])) };
}

/** Translate only binding references, including nested map body references. */
function referenceKey(value: string, keys: ReadonlyMap<string, string>): string {
  const [id, ...suffix] = value.split(".");
  return [keys.get(id!) ?? id, ...suffix].join(".");
}
function bindingReferences(value: unknown, keys: ReadonlyMap<string, string>): unknown {
  if (Array.isArray(value)) return value.map((item) => bindingReferences(item, keys));
  if (value && typeof value === "object" && Object.hasOwn(value, "value")) return value;
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([name, item]) => [
    name, name === "from" && typeof item === "string" ? referenceKey(item, keys) : bindingReferences(item, keys),
  ]));
  return value;
}

function bodyReferences(value: unknown, keys: ReadonlyMap<string, string>): unknown {
  const body = v.parse(v.looseObject({ input: v.optional(v.unknown()) }), value);
  return { ...body, ...(body.input === undefined ? {} : { input: bindingReferences(body.input, keys) }) };
}

export function studioSnapshot(values: StudioValues): FormSubmitResult<{
  draft: JsonValue; layout: Record<string, readonly [number, number]>;
  clientIdByKey: ReadonlyMap<string, string>;
}> {
  const snapshot = keyedCollectionToRecord(values.entries);
  if (snapshot.status !== "ok") return { ...snapshot, issues: studioErrors(snapshot.issues, values.entries, new Map()) };
  const { keyByClientId, clientIdByKey } = snapshot.data;
  const nodes = Object.fromEntries(values.entries.map((entry) => [entry.key, {
    ...entry.value,
    ...(entry.value.input === undefined ? {} : { input: bindingReferences(entry.value.input, keyByClientId) }),
    ...(entry.value.body === undefined ? {} : { body: bodyReferences(entry.value.body, keyByClientId) }),
    next: Object.fromEntries(Object.entries(entry.value.next).map(([port, targets]) => [port,
      (typeof targets === "string" ? [targets] : targets).map((id) => keyByClientId.get(id) ?? id),
    ])),
  }]));
  return { status: "ok", data: {
    draft: v.parse(JsonValueSchema, { ...values.document, results: bindingReferences(values.document.results ?? [], keyByClientId), nodes }),
    layout: Object.fromEntries(values.entries.flatMap(({ clientId, key }) => {
      const point = values.layout[clientId];
      return point ? [[key, [point.x, point.y] as const]] : [];
    })), clientIdByKey,
  } };
}

/** Bind submit-time keys back to the current array, even after a rename or reorder. */
export function studioErrors(
  issues: ValidationErrors, entries: readonly KeyedCollection<StudioNode>[number][], ids: ReadonlyMap<string, string>,
): ValidationErrors {
  const fields: Record<string, readonly string[]> = {};
  const formErrors = [...issues.formErrors];
  for (const [path, messages] of Object.entries(issues.fieldErrors)) {
    const parts = path.split(".");
    const clientId = parts[0] === "nodes" ? ids.get(parts[1]!) : path;
    const index = entries.findIndex((entry) => entry.clientId === clientId);
    if (index < 0) { formErrors.push(...messages); continue; }
    const suffix = parts[0] !== "nodes" || parts[2] === "[key]" ? "key"
      : parts.length > 2 ? `value.${parts.slice(2).join(".")}` : "key";
    const field = `entries.${index}.${suffix}`;
    fields[field] = [...(fields[field] ?? []), ...messages];
  }
  return { fieldErrors: fields, formErrors };
}
