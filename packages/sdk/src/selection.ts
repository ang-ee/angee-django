// Runtime GraphQL document builder.
//
// The backend owns the schema. A view supplies only a model type label and the
// dotted field paths it wants to read; this module turns those into the relay
// query, detail, mutation, and aggregate documents the schema serves. `id` is
// injected at every object level so the normalized cache always has a key.

import {
  AGGREGATE_MEASURE_OPERATORS,
  type AggregateMeasure,
  type AggregateMeasureOperator,
} from "./aggregate-extract";
import type { ModelRootFieldMetadata } from "./model-metadata";

/**
 * A node in a GraphQL selection set: a field name and, for a traversed path, an
 * ordered sub-selection. `children: undefined` marks a scalar/enum/id leaf.
 */
export interface SelectionField {
  name: string;
  children?: SelectionField[];
}

const GRAPHQL_NAME = /^[_A-Za-z][_0-9A-Za-z]*$/;
type RootFieldNameKey =
  | "detail"
  | "list"
  | "aggregate"
  | "groupBy"
  | "revisions"
  | "create"
  | "update"
  | "delete";

function assertName(name: string): string {
  if (!GRAPHQL_NAME.test(name)) {
    throw new Error(`Invalid GraphQL field name: ${name}`);
  }
  return name;
}

/**
 * Build an ordered, de-duplicated selection tree covering the requested field
 * paths. A dotted path expresses object traversal: `owner.firstName` becomes
 * `owner { id firstName }`. A bare name is a leaf — the SDK does not guess
 * whether it is an object or scalar.
 */
export function buildSelection(fieldPaths: readonly string[]): SelectionField[] {
  const root: SelectionField[] = [];
  ensureLeaf(root, "id");
  for (const path of fieldPaths) {
    addPath(
      root,
      path.split(".").filter(Boolean).map(assertName),
    );
  }
  return root;
}

function addPath(into: SelectionField[], segments: readonly string[]): void {
  const [head, ...rest] = segments;
  if (head === undefined) return;
  if (rest.length === 0) {
    ensureLeaf(into, head);
    return;
  }
  const branch = ensureBranch(into, head);
  ensureLeaf(branch, "id");
  addPath(branch, rest);
}

function ensureLeaf(into: SelectionField[], name: string): void {
  if (!into.some((node) => node.name === name)) into.push({ name });
}

function ensureBranch(into: SelectionField[], name: string): SelectionField[] {
  const existing = into.find((node) => node.name === name);
  if (existing) return (existing.children ??= []);
  const children: SelectionField[] = [];
  into.push({ name, children });
  return children;
}

/** Print a selection tree as a single-line GraphQL selection set body. */
export function printSelection(fields: readonly SelectionField[]): string {
  return fields
    .map((field) =>
      field.children && field.children.length > 0
        ? `${field.name} { ${printSelection(field.children)} }`
        : field.name,
    )
    .join(" ");
}

/**
 * The GraphQL type name for a model label. Accepts a bare type name (`Note`) or
 * a Django label whose final segment is the type (`notes.Note`); the first
 * letter is upper-cased so the result is a valid type name either way. This name
 * heads the input/order/filter/group-by types strawberry-django derives from the
 * model (`NoteInput`, `NoteOrder`, …), not the GraphQL object type (`NoteType`).
 */
export function typeNameForModel(modelLabel: string): string {
  const segment = modelLabel.split(".").pop() ?? "";
  const name = assertName(segment);
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * Encode a bare public id as the relay GlobalID a node `id` carries
 * (`btoa("DriveType:drv…")`). Relation fields (`file.drive`, `page.vault`, …)
 * come back as the bare public sqid, so normalising them up to the GlobalID lets
 * a client-side join match the related row's `id` — and keeps the value valid as
 * a GlobalID mutation input. One relay boundary, owned here.
 */
export function toRelayGlobalId(typeName: string, id: string): string {
  return btoa(`${typeName}:${id}`);
}

/**
 * Decode a relay GlobalID back to the bare public id (sqid) it wraps — the inverse
 * of {@link toRelayGlobalId}. The node `id` is the only public id a GraphQL type
 * exposes (the raw sqid is not a field), so a caller that needs the sqid (e.g. a
 * rebac resource id for a view envelope) recovers it here rather than selecting a
 * non-existent `sqid` field. Same relay boundary, owned here.
 */
export function fromRelayGlobalId(globalId: string): string {
  const decoded = atob(globalId);
  const separator = decoded.indexOf(":");
  return separator === -1 ? decoded : decoded.slice(separator + 1);
}

/** The relay GlobalID for a relation field's optional bare id, preserving null. */
export function relationRelayGlobalId(
  typeName: string,
  id: string | null | undefined,
): string | null {
  return id ? toRelayGlobalId(typeName, id) : null;
}

/**
 * The bare public id encoded inside a relay GlobalID, or `null` when the value
 * is not one. The inverse of `toRelayGlobalId`: decode `btoa("DriveType:drv…")`
 * back to the `drv…` suffix. The same relay boundary owns both directions, so a
 * client showing the human-facing id never re-implements the `atob` decode.
 */
export function relayGlobalIdSuffix(value: string): string | null {
  let decoded: string;
  try {
    decoded = typeof atob === "function" ? atob(value.trim()) : "";
  } catch {
    return null;
  }
  const separator = decoded.indexOf(":");
  if (separator <= 0) return null;
  if (!/^[A-Za-z][A-Za-z0-9]*$/.test(decoded.slice(0, separator))) return null;
  const suffix = decoded.slice(separator + 1).trim();
  return suffix === "" ? null : suffix;
}

function requireRootField(
  modelLabel: string,
  rootFields: ModelRootFieldMetadata | null | undefined,
  key: RootFieldNameKey,
): string {
  const field = rootFields?.[key];
  if (!field) {
    throw new Error(
      `GraphQL schema metadata for model "${modelLabel}" does not expose a ${key} root field.`,
    );
  }
  return assertName(field);
}

/** Relay detail document using the schema-declared detail root field. */
export function assembleDetailDocument(
  modelLabel: string,
  fieldPaths: readonly string[],
  rootFields: ModelRootFieldMetadata,
): string {
  const detail = requireRootField(modelLabel, rootFields, "detail");
  const selection = printSelection(buildSelection(fieldPaths));
  return `query ${detail}($id: ID!) { ${detail}(id: $id) { ${selection} } }`;
}

/** Newest-first revision document using the schema-declared revisions root. */
export function assembleRevisionsDocument(
  modelLabel: string,
  fieldPaths: readonly string[],
  rootFields: ModelRootFieldMetadata,
): string {
  const revisions = requireRootField(modelLabel, rootFields, "revisions");
  const selection = printSelection(buildSelection(fieldPaths));
  return `query ${revisions}($id: ID!) { ${revisions}(id: $id) { ${selection} } }`;
}

export interface AssembleListDocumentOptions {
  /** Declare `$filters: <Type>Filter` and pass it to the connection. */
  withFilter?: boolean;
  /** Declare `$order: <Type>Order` and pass it to the connection. */
  withOrder?: boolean;
}

export interface AssembleGroupByDocumentOptions {
  /** Select these fields from the grouped row's `key` object. */
  keyFields: readonly string[];
  /** Select these aggregate measures from every grouped bucket. */
  measures?: readonly AggregateMeasure[];
  /** Declare `$filter: <Type>Filter` and pass it to the grouped field. */
  withFilter?: boolean;
  /** Declare `$orderBy: [<Type>GroupOrder!]` and pass it to the grouped field. */
  withOrderBy?: boolean;
  /** Select the grouped row's echoed list filter when the backend exposes it. */
  withFilterEcho?: boolean;
}

export interface AssembleAggregateDocumentOptions {
  /** Declare `$filter: <Type>Filter` and pass it to the aggregate field. */
  withFilter?: boolean;
  /** Select these aggregate measures from the ungrouped aggregate. */
  measures?: readonly AggregateMeasure[];
}

/**
 * Offset-paginated list document. Pages with `pagination: { offset, limit }`, so
 * the client jumps to any page (`offset = (page - 1) * limit`). Selects
 * `totalCount` — the backend owns the count, from which the client derives the
 * page count — plus the page `results` and the echoed `offset`/`limit`.
 */
export function assembleListDocument(
  modelLabel: string,
  fieldPaths: readonly string[],
  rootFields: ModelRootFieldMetadata,
  options: AssembleListDocumentOptions = {},
): string {
  const typeName = typeNameForModel(modelLabel);
  const list = requireRootField(modelLabel, rootFields, "list");
  const selection = printSelection(buildSelection(fieldPaths));
  const declared = ["$pagination: OffsetPaginationInput"];
  const args = ["pagination: $pagination"];
  if (options.withFilter) {
    declared.push(`$filters: ${typeName}Filter`);
    args.push("filters: $filters");
  }
  if (options.withOrder) {
    declared.push(`$order: ${typeName}Order`);
    args.push("order: $order");
  }
  return (
    `query ${list}(${declared.join(", ")}) { ` +
    `${list}(${args.join(", ")}) { ` +
    `totalCount results { ${selection} } pageInfo { offset limit } } }`
  );
}

export type MutationAction = "create" | "update" | "delete";

// The cascade summary a delete returns: counts grouped by model label, plus a
// bounded tree of records the backend would delete.
const DELETE_PREVIEW_SELECTION =
  "totalDeletedCount hasBlockers " +
  "deleted { label count } updated { label count } blocked { label count } " +
  "root { label objectLabel objectId " +
  "children { label objectLabel objectId " +
  "children { label objectLabel objectId } } }";

/**
 * CRUD mutation using the schema-declared root field for the action. `create`
 * takes a `<Type>Input`; `update` takes a `<Type>Patch` carrying its own id, so
 * it needs no separate id variable; both return the mutated node's selection.
 * `delete` takes an id and returns the cascade `DeletePreview`.
 */
export function assembleMutationDocument(
  modelLabel: string,
  action: MutationAction,
  fieldPaths: readonly string[],
  rootFields: ModelRootFieldMetadata,
): string {
  const typeName = typeNameForModel(modelLabel);
  const op = requireRootField(modelLabel, rootFields, action);
  if (action === "delete") {
    return (
      `mutation ${op}($id: ID!, $confirm: Boolean) { ` +
      `${op}(id: $id, confirm: $confirm) { ${DELETE_PREVIEW_SELECTION} } }`
    );
  }
  const inputType = action === "create" ? `${typeName}Input` : `${typeName}Patch`;
  const selection = printSelection(buildSelection(fieldPaths));
  return (
    `mutation ${op}($data: ${inputType}!) { ${op}(data: $data) { ${selection} } }`
  );
}

/** The ungrouped aggregate document selects the model total count and measures. */
export function assembleAggregateDocument(
  modelLabel: string,
  rootFields: ModelRootFieldMetadata,
  options: AssembleAggregateDocumentOptions = {},
): string {
  const typeName = typeNameForModel(modelLabel);
  const field = requireRootField(modelLabel, rootFields, "aggregate");
  const selection = aggregateSelection(options.measures);
  if (!options.withFilter) {
    return `query ${field} { ${field} { ${selection} } }`;
  }
  return `query ${field}($filter: ${typeName}Filter) { ${field}(filter: $filter) { ${selection} } }`;
}

/**
 * The grouped aggregate document selects an offset-paginated list of grouped
 * buckets. The caller supplies both the backend group spec variable and the
 * key fields it wants rendered back from each row.
 */
export function assembleGroupByDocument(
  modelLabel: string,
  rootFields: ModelRootFieldMetadata,
  options: AssembleGroupByDocumentOptions,
): string {
  const typeName = typeNameForModel(modelLabel);
  const field = requireRootField(modelLabel, rootFields, "groupBy");
  const groupByInput = assertName(rootFields.groupByInput ?? `${typeName}GroupBySpec`);
  const keyFields = [...new Set(options.keyFields.map(assertName))];
  const keySelection = keyFields.length > 0 ? keyFields.join(" ") : "__typename";
  const measureSelection = aggregateMeasureSelection(options.measures);
  const declared = [
    `$groupBy: [${groupByInput}!]!`,
    "$pagination: OffsetPaginationInput",
  ];
  const args = ["groupBy: $groupBy", "pagination: $pagination"];
  if (options.withFilter) {
    declared.push(`$filter: ${typeName}Filter`);
    args.push("filter: $filter");
  }
  if (options.withOrderBy) {
    const groupOrderInput = assertName(
      rootFields.groupOrderInput ?? `${typeName}GroupOrder`,
    );
    declared.push(`$orderBy: [${groupOrderInput}!]`);
    args.push("orderBy: $orderBy");
  }
  const resultSelection = [
    `key { ${keySelection} }`,
    "count",
    ...(options.withFilterEcho ? ["filter"] : []),
    ...(measureSelection ? [measureSelection] : []),
  ].join(" ");
  return (
    `query ${field}(${declared.join(", ")}) { ` +
    `${field}(${args.join(", ")}) { ` +
    `totalCount results { ${resultSelection} } ` +
    `pageInfo { offset limit } } }`
  );
}

function aggregateSelection(measures: readonly AggregateMeasure[] | undefined): string {
  const measureSelection = aggregateMeasureSelection(measures);
  return measureSelection ? `count ${measureSelection}` : "count";
}

function aggregateMeasureSelection(
  measures: readonly AggregateMeasure[] | undefined,
): string {
  if (!measures || measures.length === 0) return "";
  const fieldsByOp = new Map<AggregateMeasureOperator, string[]>();
  for (const measure of measures) {
    const field = assertName(measure.field);
    const fields = fieldsByOp.get(measure.op) ?? [];
    if (!fields.includes(field)) fields.push(field);
    fieldsByOp.set(measure.op, fields);
  }
  return AGGREGATE_MEASURE_OPERATORS.flatMap((op) => {
    const fields = fieldsByOp.get(op);
    return fields && fields.length > 0 ? [`${op} { ${fields.join(" ")} }`] : [];
  }).join(" ");
}

export const MAX_PAGE_SIZE = 100;
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 80, MAX_PAGE_SIZE] as const;
export const DEFAULT_PAGE_SIZE = PAGE_SIZE_OPTIONS[2];

/** Clamp a requested page size (the offset `limit`) to `[1, MAX_PAGE_SIZE]`. */
export function clampPageSize(pageSize: number): number {
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.floor(pageSize)));
}
