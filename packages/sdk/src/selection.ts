// Runtime GraphQL document builder.
//
// The backend owns the schema. A view supplies only a model type label and the
// dotted field paths it wants to read; this module turns those into the relay
// query, detail, mutation, and aggregate documents the schema serves. `id` is
// injected at every object level so the normalized cache always has a key.

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
 * letter is upper-cased so the result is a valid type name either way.
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

function pluralize(value: string): string {
  const last = value.at(-1);
  const prev = value.at(-2);
  if (last === "y" && prev !== undefined && !"aeiou".includes(prev)) {
    return `${value.slice(0, -1)}ies`;
  }
  if (last === "s" || last === "x" || last === "z") return `${value}es`;
  return `${value}s`;
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Relay detail document: `query <singular>($id: Sqid!){ <singular>(id:){…} }`. */
export function assembleDetailDocument(
  modelLabel: string,
  fieldPaths: readonly string[],
): string {
  const singular = singularFieldName(modelLabel);
  const selection = printSelection(buildSelection(fieldPaths));
  return `query ${singular}($id: Sqid!) { ${singular}(id: $id) { ${selection} } }`;
}

export interface AssembleListDocumentOptions {
  /** Declare `$filters: <Type>Filter` and pass it to the connection. */
  withFilter?: boolean;
  /** Declare `$order: [<Type>Order!]` and pass it to the connection. */
  withOrder?: boolean;
}

/** Relay connection document with `totalCount / edges { node } / pageInfo`. */
export function assembleListDocument(
  modelLabel: string,
  fieldPaths: readonly string[],
  options: AssembleListDocumentOptions = {},
): string {
  const typeName = typeNameForModel(modelLabel);
  const plural = pluralFieldName(modelLabel);
  const selection = printSelection(buildSelection(fieldPaths));
  const declared = ["$first: Int", "$after: String", "$search: String"];
  const args = ["search: $search", "first: $first", "after: $after"];
  if (options.withFilter) {
    declared.push(`$filters: ${typeName}Filter`);
    args.push("filters: $filters");
  }
  if (options.withOrder) {
    declared.push(`$order: [${typeName}Order!]`);
    args.push("order: $order");
  }
  return (
    `query ${plural}(${declared.join(", ")}) { ` +
    `${plural}(${args.join(", ")}) { ` +
    `totalCount edges { node { ${selection} } } ` +
    `pageInfo { endCursor hasNextPage } } }`
  );
}

export type MutationAction = "create" | "update" | "delete";

/**
 * Noun-first CRUD mutation. `create`/`update` return the mutated node's
 * selection; `delete` returns the `DeletePreview` `{ ok id }` shape.
 */
export function assembleMutationDocument(
  modelLabel: string,
  action: MutationAction,
  fieldPaths: readonly string[],
): string {
  const singular = singularFieldName(modelLabel);
  const typeName = typeNameForModel(modelLabel);
  const op = `${singular}${capitalize(action)}`;
  if (action === "delete") {
    return `mutation ${op}($id: Sqid!) { ${op}(id: $id) { ok id } }`;
  }
  const selection = printSelection(buildSelection(fieldPaths));
  if (action === "create") {
    return (
      `mutation ${op}($input: ${typeName}CreateInput!) { ` +
      `${op}(input: $input) { ${selection} } }`
    );
  }
  return (
    `mutation ${op}($id: Sqid!, $input: ${typeName}UpdateInput!) { ` +
    `${op}(id: $id, input: $input) { ${selection} } }`
  );
}

/** Single-row aggregate field name (`Sale` -> `salesAggregate`). */
export function aggregateFieldName(modelLabel: string): string {
  return `${pluralFieldName(modelLabel)}Aggregate`;
}

/** Grouped aggregate field name (`Sale` -> `salesGroupBy`). */
export function groupByFieldName(modelLabel: string): string {
  return `${pluralFieldName(modelLabel)}GroupBy`;
}

export function assembleAggregateDocument(
  modelLabel: string,
  measureFields: readonly string[] = [],
): string {
  const field = aggregateFieldName(modelLabel);
  const measures = printMeasures(measureFields);
  return (
    `query ${field}($search: String) { ` +
    `${field}(search: $search) { count${measures ? ` ${measures}` : ""} } }`
  );
}

export function assembleGroupByDocument(
  modelLabel: string,
  keyFields: readonly string[],
  measureFields: readonly string[] = [],
): string {
  const typeName = typeNameForModel(modelLabel);
  const field = groupByFieldName(modelLabel);
  const keySelection = keyFields.map(assertName).join(" ");
  const measures = printMeasures(measureFields);
  return (
    `query ${field}($groupBy: [${typeName}GroupBySpec!]!, $search: String) { ` +
    `${field}(groupBy: $groupBy, search: $search) { ` +
    `totalCount results { key { ${keySelection} } ` +
    `count${measures ? ` ${measures}` : ""} } } }`
  );
}

/** The four numeric measure operators, each over the same field set. */
const MEASURE_OPERATORS = ["sum", "avg", "min", "max"] as const;

function printMeasures(measureFields: readonly string[]): string {
  const fields = [...new Set(measureFields.map(assertName))];
  if (fields.length === 0) return "";
  const body = fields.join(" ");
  return MEASURE_OPERATORS.map((op) => `${op} { ${body} }`).join(" ");
}

/** Relay connection arguments derived from offset-style `page`/`pageSize`. */
export interface ConnectionArgs {
  first: number;
  after: string | null;
}

export const RELAY_MAX_PAGE_SIZE = 100;
export const RELAY_PAGE_SIZE_OPTIONS = [10, 20, 50, 80, RELAY_MAX_PAGE_SIZE] as const;

/**
 * Translate 1-based `page`/`pageSize` to relay `{ first, after }`. The cursor
 * encodes the offset of the previous page's last row, so jump-to-page works
 * against the schema's offset-backed relay cursor.
 */
export function pageToConnectionArgs(
  page: number,
  pageSize: number,
): ConnectionArgs {
  const safePage = Math.max(1, Math.floor(page));
  const safeSize = Math.min(RELAY_MAX_PAGE_SIZE, Math.max(1, Math.floor(pageSize)));
  const offset = (safePage - 1) * safeSize;
  return {
    first: safeSize,
    after: offset === 0 ? null : encodeOffsetCursor(offset - 1),
  };
}

/** base64 of `arrayconnection:<index>` — the schema's relay cursor encoding. */
export function encodeOffsetCursor(index: number): string {
  return btoa(`arrayconnection:${index}`);
}
