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

/**
 * A node in a GraphQL selection set: a field name and, for a traversed path, an
 * ordered sub-selection. `children: undefined` marks a scalar/enum/id leaf.
 */
export interface SelectionField {
  name: string;
  children?: SelectionField[];
}

const GRAPHQL_NAME = /^[_A-Za-z][_0-9A-Za-z]*$/;

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

/** Singular root field name (`Note` -> `note`), matching the schema default. */
export function singularFieldName(modelLabel: string): string {
  const name = typeNameForModel(modelLabel);
  return name.charAt(0).toLowerCase() + name.slice(1);
}

/** Plural connection field name (`Note` -> `notes`, `Category` -> `categories`). */
export function pluralFieldName(modelLabel: string): string {
  return pluralize(singularFieldName(modelLabel));
}

// Regular English pluralization, matching the schema's default field naming.
// Irregular plurals (person -> people) are not derivable from a heuristic and
// belong to the backend; a model whose plural is irregular needs its field name
// emitted by the contract rather than guessed here.
function pluralize(value: string): string {
  const last = value.at(-1);
  const prev = value.at(-2);
  if (last === "y" && prev !== undefined && !"aeiou".includes(prev)) {
    return `${value.slice(0, -1)}ies`;
  }
  // A single trailing `z` after a vowel doubles (quiz -> quizzes); `zz`
  // (buzz) and a `z` after a consonant (waltz) just take `es`.
  if (last === "z" && prev !== undefined && "aeiou".includes(prev)) {
    return `${value}zes`;
  }
  if (last === "s" || last === "x" || last === "z") return `${value}es`;
  return `${value}s`;
}

/** Relay detail document: `query <singular>($id: ID!){ <singular>(id:){…} }`. */
export function assembleDetailDocument(
  modelLabel: string,
  fieldPaths: readonly string[],
): string {
  const singular = singularFieldName(modelLabel);
  const selection = printSelection(buildSelection(fieldPaths));
  return `query ${singular}($id: ID!) { ${singular}(id: $id) { ${selection} } }`;
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
  options: AssembleListDocumentOptions = {},
): string {
  const typeName = typeNameForModel(modelLabel);
  const plural = pluralFieldName(modelLabel);
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
    `query ${plural}(${declared.join(", ")}) { ` +
    `${plural}(${args.join(", ")}) { ` +
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
 * Verb-first CRUD mutation. `create` takes a `<Type>Input`; `update` takes a
 * `<Type>Patch` carrying its own id, so it needs no separate id variable; both
 * return the mutated node's selection. `delete` takes an id and returns the
 * cascade `DeletePreview`.
 */
export function assembleMutationDocument(
  modelLabel: string,
  action: MutationAction,
  fieldPaths: readonly string[],
): string {
  const typeName = typeNameForModel(modelLabel);
  const op = `${action}${typeName}`;
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

/** Aggregate field name (`Sale` -> `saleAggregate`). */
export function aggregateFieldName(modelLabel: string): string {
  return `${singularFieldName(modelLabel)}Aggregate`;
}

/** Grouped-rows field name (`Sale` -> `saleGroups`). */
export function groupByFieldName(modelLabel: string): string {
  return `${singularFieldName(modelLabel)}Groups`;
}

/** The ungrouped aggregate document selects the model total count and measures. */
export function assembleAggregateDocument(
  modelLabel: string,
  options: AssembleAggregateDocumentOptions = {},
): string {
  const typeName = typeNameForModel(modelLabel);
  const field = aggregateFieldName(modelLabel);
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
  options: AssembleGroupByDocumentOptions,
): string {
  const typeName = typeNameForModel(modelLabel);
  const field = groupByFieldName(modelLabel);
  const keyFields = [...new Set(options.keyFields.map(assertName))];
  const keySelection = keyFields.length > 0 ? keyFields.join(" ") : "__typename";
  const measureSelection = aggregateMeasureSelection(options.measures);
  const declared = [
    `$groupBy: [${typeName}GroupBySpec!]!`,
    "$pagination: OffsetPaginationInput",
  ];
  const args = ["groupBy: $groupBy", "pagination: $pagination"];
  if (options.withFilter) {
    declared.push(`$filter: ${typeName}Filter`);
    args.push("filter: $filter");
  }
  if (options.withOrderBy) {
    declared.push(`$orderBy: [${typeName}GroupOrder!]`);
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
