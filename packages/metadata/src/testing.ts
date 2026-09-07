import type {
  DataResourceMetadata,
  ModelMetadata,
  SchemaFieldMetadata,
} from "./artifact";
import type { DataResourceQuery } from "./query-schema";
import { modelLabelSegment } from "./naming";

/** Minimal generated-resource fixture shared by framework package tests. */
export function testDataResource(
  modelLabel: string,
  overrides: Partial<DataResourceMetadata> = {},
): DataResourceMetadata {
  const segment = modelLabelSegment(modelLabel);
  const modelName = overrides.modelName ?? segment.toLowerCase();
  const separator = modelLabel.lastIndexOf(".");
  const { roots, typeNames, ...rest } = overrides;
  const list = `${modelName}s`;
  return {
    schemaName: "console",
    modelLabel,
    appLabel: separator < 0 ? "" : modelLabel.slice(0, separator),
    modelName,
    roots: {
      list,
      detail: `${list}_by_pk`,
      create: `insert_${list}_one`,
      update: `update_${list}_by_pk`,
      delete: `delete_${list}_by_pk`,
      ...roots,
    },
    typeNames: { node: `${segment}Type`, ...typeNames },
    capabilities: ["list", "detail", "create", "update", "delete"],
    fields: [],
    query: testResourceQuery(),
    aggregateFields: [],
    ...rest,
  };
}

/**
 * Fill the canonical resource/label indexes for a hand-authored test metadata
 * object whose model entries already carry their resource facts.
 */
export function withTestResourceInventory(
  metadata: {
    types: Readonly<Record<string, ModelMetadata>>;
  },
): SchemaFieldMetadata {
  const types: Record<string, ModelMetadata> = {};
  const labels: Record<string, ModelMetadata> = {};
  const resources: DataResourceMetadata[] = [];
  for (const model of Object.values(metadata.types)) {
    const indexed: ModelMetadata = model;
    resources.push(indexed.resource);
    labels[indexed.resource.modelLabel] = indexed;
    const nodeName = indexed.resource.typeNames.node;
    if (nodeName) types[nodeName] = indexed;
  }
  return { types, labels, resources };
}

/** Explicit query fixture; callers supply executable capabilities for their case. */
export function testResourceQuery(overrides: Partial<DataResourceQuery> = {}): DataResourceQuery {
  return { identity: { field: "id" }, fields: {}, axes: {}, sort: { default: [] }, paging: {}, ...overrides };
}

/** A canonical string query field; override capabilities for the behavior under test. */
export function testQueryField(
  name: string,
  overrides: Partial<import("./query-schema").QueryField> = {},
): import("./query-schema").QueryField {
  return {
    kind: "scalar", scalar: "String", values: [], nullable: true,
    row: { path: name, paths: [name] },
    filter: { field: name, scalar: "String", values: [], operators: ["exact", "inList", "isNull"] },
    ...overrides,
  };
}

/** An explicit client axis; server input and result names must be supplied. */
export function testQueryAxis(
  field: string,
  overrides: Partial<import("./query-schema").QueryAxis> = {},
): import("./query-schema").QueryAxis {
  return { field, kind: "column", identityPath: field, paths: [field], extractions: [], ...overrides };
}
