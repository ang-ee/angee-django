import { storyField, testDataResource, testQueryField, testResourceQuery, type GraphQLWireOperation } from "./runtime-fixtures";

/** The implementations the registry names, as the column's GraphQL enum spells them. */
const registered = [{ value: "LOCAL", description: "Local folder" }, { value: "S3", description: "S3 bucket" }];

/**
 * Storage backends whose `backend_class` is an implementation-key column. The second stores a key
 * the registry no longer names, so the column reads null for it, as the server reads it.
 */
export const backends = [
  { id: "backend-1", label: "Team files", backend_class: "LOCAL", config: { root: "/srv/team", create_dirs: true } },
  { id: "backend-2", label: "Old archive", backend_class: null, config: { root: "/srv/archive", retired_flag: true } },
] as const;

/**
 * Each implementation's config fields, as `impl_choices` describes them (a FormSpec). They share `root`, declared
 * alike, so carrying it over shows; each has one field of its own with a default, so a default shows too.
 */
const configSchemas = {
  local: { type: "object", properties: {
    root: { type: "string", label: "Root" },
    create_dirs: { type: "boolean", label: "Create folders", defaultValue: true },
  } },
  s3: { type: "object", properties: {
    root: { type: "string", label: "Root" },
    bucket: { type: "string", label: "Bucket", defaultValue: "archive" },
  } },
} as const;

/** What choosing each implementation sets on an insert: its defaults (the server's `effective_defaults`). */
const insertDefaults = { local: { config: { root: "", create_dirs: true } }, s3: { config: { root: "", bucket: "archive" } } };

/**
 * What choosing each implementation sets on Old archive, a repair, written out as the server's rule answers it
 * (`ImplBase.carried_config`): `root` kept, the chosen class's own field at its default, `retired_flag` dropped.
 * The shape is the read's contract, `defaults: {<config field>: <carried config>}`, the one the server's
 * `test_a_writer_reads_what_each_implementation_sets_on_a_retired_bridge_and_is_refused_as_the_write_is` pins.
 */
export const repairDefaults = {
  local: { config: { root: "/srv/archive", create_dirs: true } },
  s3: { config: { root: "/srv/archive", bucket: "archive" } },
} as const;

/**
 * `storage.Backend`, whose `implFields` names `backend_class`: a null there is a key not registered. With
 * `createOnly`, the model makes the selector create-only, as its metadata says (`createOnlyBackendResource`).
 */
function backendResourceFor({ createOnly = false }: { createOnly?: boolean } = {}) {
  return testDataResource("storage.Backend", {
    schemaName: "public", recordRepresentation: "label", implFields: ["backend_class"],
    // A create-only selector beside the update root: the resource generates its repair read.
    roots: { aggregate: "backends_aggregate", ...(createOnly ? { repairChoices: "backends_repair_choices" } : {}) },
    typeNames: { filter: "BackendFilter", order: "BackendOrder" },
    fields: [
      storyField("id", { scalar: "ID", creatable: false, updatable: false }), storyField("label"),
      storyField("backend_class", { kind: "enum", scalar: null, values: registered, ...(createOnly ? { createOnly } : {}) }),
      storyField("config", { scalar: "JSON" }),
    ],
    query: testResourceQuery({ fields: {
      id: testQueryField("id", { scalar: "ID" }),
      label: testQueryField("label", { sort: { field: "label" } }),
      backend_class: testQueryField("backend_class", { kind: "enum", values: registered, sort: { field: "backend_class" } }),
    } }),
  });
}

export const backendResource = backendResourceFor();
/** The same resource with its selector create-only on the model, as the server's metadata publishes it. */
export const createOnlyBackendResource = backendResourceFor({ createOnly: true });

/**
 * A fresh set of the backends' roots, with its own store of what its saves write, so a saved choice reads
 * back as the server would read it within one story and never leaks into another. A save keeps the key a
 * backend holds unless it chooses another. `impl_choices` answers a create's choices; the resource's repair
 * read, `backends_repair_choices(id, field)`, answers, as the server does, only for a backend whose stored
 * key is not registered: what choosing each implementation sets on it.
 */
export function backendOperationsFor(): Readonly<Record<string, GraphQLWireOperation>> {
  const stored = new Map<string, Record<string, unknown>>(backends.map((backend) => [backend.id, { ...backend }]));
  const backendById = (args: Record<string, unknown>) => {
    const keys = args.pk_columns;
    const id = typeof keys === "object" && keys !== null && "id" in keys ? keys.id : args.id;
    return typeof id === "string" ? stored.get(id) ?? null : null;
  };
  return {
    impl_choices: () => registered.map(({ value }) => {
      const key = value.toLowerCase() as keyof typeof configSchemas;
      return { key, category: "", config_schema: configSchemas[key], defaults: insertDefaults[key] };
    }),
    backends_repair_choices: (args) => {
      const record = typeof args.id === "string" ? stored.get(args.id) : undefined;
      if (args.field !== "backend_class" || record?.id !== "backend-2" || record.backend_class != null) {
        throw new Error("Implementation selection is create-only.");
      }
      return registered.map(({ value }) => {
        const key = value.toLowerCase() as keyof typeof configSchemas;
        return { key, category: "", config_schema: configSchemas[key], defaults: repairDefaults[key] };
      });
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
}

/** The backends' roots shared by stories that never save a backend; a story that saves one takes its own (`backendOperationsFor`). */
export const backendOperations = backendOperationsFor();
