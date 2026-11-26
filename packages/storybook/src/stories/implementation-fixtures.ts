import type { DataResourceFieldMetadata } from "@angee/metadata";

import { GraphQLError, testDataResource, testQueryField, testResourceQuery, type GraphQLWireOperation } from "./runtime-fixtures";

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

const field = (name: string, overrides: Partial<DataResourceFieldMetadata> = {}): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: "String", readable: true, aggregatable: false,
  creatable: true, updatable: true, requiredOnCreate: false, ...overrides,
});

/** `storage.Backend`, whose `implFields` names `backend_class`: a null there is a key not registered. */
export const backendResource = testDataResource("storage.Backend", {
  schemaName: "public", recordRepresentation: "label", implFields: ["backend_class"],
  roots: { aggregate: "backends_aggregate" }, typeNames: { filter: "BackendFilter", order: "BackendOrder" },
  fields: [
    field("id", { scalar: "ID", creatable: false, updatable: false }), field("label"),
    field("backend_class", { kind: "enum", scalar: null, values: registered }),
  ],
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID" }),
    label: testQueryField("label", { sort: { field: "label" } }),
    backend_class: testQueryField("backend_class", { kind: "enum", values: registered, sort: { field: "backend_class" } }),
  } }),
});

const backendById = (args: Record<string, unknown>) => {
  const keys = args.pk_columns;
  const id = typeof keys === "object" && keys !== null && "id" in keys ? keys.id : args.id;
  return backends.find((backend) => backend.id === id) ?? null;
};

/**
 * The backends' roots. A save keeps the key the backend holds unless it chooses another, as the
 * server does: choosing a key the registry does not name is refused under the key's field.
 */
export const backendOperations: Readonly<Record<string, GraphQLWireOperation>> = {
  backends: () => backends,
  backends_aggregate: () => ({ aggregate: { count: backends.length } }),
  backends_by_pk: backendById,
  update_backends_by_pk: (args) => {
    const patch: Record<string, unknown> = typeof args._set === "object" && args._set !== null ? { ...args._set } : {};
    const saved = { ...backendById(args), ...patch };
    if (!("backend_class" in patch) || registered.some(({ value }) => value === patch.backend_class)) return saved;
    const refusal = `Value '${String(patch.backend_class)}' is not a valid choice.`;
    throw new GraphQLError(refusal, {
      extensions: { code: "VALIDATION", validationErrors: { backend_class: [refusal] }, formErrors: [] },
    });
  },
};
