import { storyField, testDataResource, testQueryField, testResourceQuery, type GraphQLWireOperation } from "./runtime-fixtures";

/** The implementations the registry names, as the column's GraphQL enum spells them. */
const registered = [{ value: "LOCAL", description: "Local folder" }, { value: "S3", description: "S3 bucket" }];

/**
 * Storage backends whose `backend_class` is an implementation-key column. The second stores a key
 * the registry no longer names, so the column reads null for it, as the server reads it.
 */
export const backends = [
  { id: "backend-1", label: "Team files", backend_class: "LOCAL" },
  { id: "backend-2", label: "Old archive", backend_class: null },
] as const;

/** `storage.Backend`, whose `implFields` names `backend_class`: a null there is a key not registered. */
export const backendResource = testDataResource("storage.Backend", {
  schemaName: "public", recordRepresentation: "label", implFields: ["backend_class"],
  roots: { aggregate: "backends_aggregate" }, typeNames: { filter: "BackendFilter", order: "BackendOrder" },
  fields: [
    storyField("id", { scalar: "ID", creatable: false, updatable: false }), storyField("label"),
    storyField("backend_class", { kind: "enum", scalar: null, values: registered }),
  ],
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID" }),
    label: testQueryField("label", { sort: { field: "label" } }),
    backend_class: testQueryField("backend_class", { kind: "enum", values: registered, sort: { field: "backend_class" } }),
  } }),
});

// What the stories' saves write, by id, so a saved choice reads back as the server would read it.
const stored = new Map<string, Record<string, unknown>>(backends.map((backend) => [backend.id, { ...backend }]));

const backendById = (args: Record<string, unknown>) => {
  const keys = args.pk_columns;
  const id = typeof keys === "object" && keys !== null && "id" in keys ? keys.id : args.id;
  return typeof id === "string" ? stored.get(id) ?? null : null;
};

/**
 * The backends' roots. A save writes what it sends and keeps the key the backend holds unless it chooses
 * another. `impl_choices` with a record answers, as the server does, only for a backend whose stored key
 * is not registered: what choosing each implementation sets on it (these backends carry no config).
 */
export const backendOperations: Readonly<Record<string, GraphQLWireOperation>> = {
  impl_choices: (args) => {
    const record = typeof args.record === "string" ? stored.get(args.record) : undefined;
    if (args.record != null && record?.backend_class != null) throw new Error("Implementation selection is create-only.");
    return registered.map(({ value }) => ({ key: value.toLowerCase(), category: "", defaults: {}, config_schema: null }));
  },
  backends: () => [...stored.values()],
  backends_aggregate: () => ({ aggregate: { count: stored.size } }),
  backends_by_pk: backendById,
  update_backends_by_pk: (args) => {
    const current = backendById(args);
    if (!current) return null;
    const saved = { ...current, ...(typeof args._set === "object" && args._set !== null ? args._set : {}) };
    stored.set(String(saved.id), saved);
    return saved;
  },
};
