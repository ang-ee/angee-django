import { parse, type DocumentNode } from "graphql";
import type { MetaQuery } from "@refinedev/core";
import type {
  DataResourceAggregateMeasureMetadata,
  DataResourceMetadata,
} from "@angee/sdk";
import { buildSelection, printSelection } from "./selection";

const GRAPHQL_NAME = /^[_A-Za-z][_0-9A-Za-z]*$/;

export const AGGREGATE_MEASURE_OPERATORS = [
  "sum",
  "avg",
  "min",
  "max",
] as const;

export type AggregateMeasureOperator =
  | "count"
  | (typeof AGGREGATE_MEASURE_OPERATORS)[number];

export interface AggregateMeasure {
  op: AggregateMeasureOperator;
  field?: string | null;
  input?: string | null;
}

export type AggregateMeasureValues = Record<string, unknown>;

export interface AggregateBucket {
  key: Record<string, unknown> | null;
  count: number;
  sum?: AggregateMeasureValues;
  avg?: AggregateMeasureValues;
  min?: AggregateMeasureValues;
  max?: AggregateMeasureValues;
}

export interface GroupByResult {
  count: number;
  /** Hasura `_groups` returns only the current window, so this is known only when a caller supplies it. */
  totalCount?: number;
  buckets: readonly AggregateBucket[];
}

export interface GroupDimension {
  /** Backend enum value passed as one `<resource>_groups(group_by: ...)` field. */
  input: string;
  /** Typed key field selected from `<resource>_group.key`. Defaults to `input`. */
  key?: string;
  /** Optional `Granularity` enum value for date/datetime group specs. */
  granularity?: string | null;
  /** Optional typed key range sibling for date/datetime bucket drilldown. */
  rangeKey?: string | null;
}

export interface AggregateRequestOptions {
  measures?: readonly AggregateMeasure[];
  where?: Record<string, unknown>;
}

export interface ListRequestOptions {
  fields: readonly string[];
  where?: Record<string, unknown>;
  orderBy?: Record<string, unknown>;
}

export interface GroupByRequestOptions extends AggregateRequestOptions {
  dimensions: readonly GroupDimension[];
  page?: number;
  pageSize?: number;
}

export interface FacetRequestSpec extends GroupByRequestOptions {
  id: string;
  valueKey?: string;
  labelKey?: string;
}

export interface FacetsRequestOptions {
  facets: readonly FacetRequestSpec[];
}

export interface ResourceFacetOption {
  value: string;
  label: string;
  count: number;
  key: Record<string, unknown>;
}

export interface ResourceFacetResult {
  count: number;
  totalCount?: number;
  options: readonly ResourceFacetOption[];
}

export interface DeletePreviewVariables {
  id: string;
  confirm?: boolean;
}

export interface DeletePreviewGroup {
  label: string;
  count: number;
}

export interface DeletePreviewNode {
  label: string;
  objectLabel: string;
  objectId: string | null;
  children: readonly DeletePreviewNode[];
}

export interface DeletePreview {
  totalDeletedCount: number;
  deleted: readonly DeletePreviewGroup[];
  updated: readonly DeletePreviewGroup[];
  blocked: readonly DeletePreviewGroup[];
  hasBlockers: boolean;
  root: DeletePreviewNode;
}

export interface ResourceRevision extends Record<string, unknown> {
  id: string;
  createdAt: string;
  comment: string | null;
}

export interface CustomGraphQLRequest {
  dataProviderName: string;
  root: string;
  meta: MetaQuery;
}

export interface CustomGraphQLMutationRequest {
  dataProviderName: string;
  root: string;
  meta: MetaQuery;
}

export function aggregateRequest(
  resource: DataResourceMetadata,
  options: AggregateRequestOptions = {},
): CustomGraphQLRequest {
  const root = requiredRoot(resource, "aggregate");
  const withWhere = options.where !== undefined;
  return {
    dataProviderName: resource.schemaName,
    root,
    meta: queryMeta(
      aggregateDocument(resource, { withWhere, measures: options.measures }),
      withWhere ? { where: options.where } : {},
    ),
  };
}

export function listRequest(
  resource: DataResourceMetadata,
  options: ListRequestOptions,
): CustomGraphQLRequest {
  const root = requiredRoot(resource, "list");
  return {
    dataProviderName: resource.schemaName,
    root,
    meta: queryMeta(
      listDocument(resource, {
        fields: options.fields,
        withWhere: options.where !== undefined,
        withOrderBy: options.orderBy !== undefined,
      }),
      listVariables(options),
    ),
  };
}

export function groupByRequest(
  resource: DataResourceMetadata,
  options: GroupByRequestOptions,
): CustomGraphQLRequest {
  const root = requiredRoot(resource, "groups");
  const withWhere = options.where !== undefined;
  const withPagination = options.pageSize !== undefined;
  return {
    dataProviderName: resource.schemaName,
    root,
    meta: queryMeta(
      groupByDocument(resource, {
        dimensions: options.dimensions,
        withWhere,
        withPagination,
        measures: options.measures,
      }),
      groupByVariables(options),
    ),
  };
}

export function facetsRequest(
  resource: DataResourceMetadata,
  options: FacetsRequestOptions,
): CustomGraphQLRequest {
  const root = requiredRoot(resource, "groups");
  return {
    dataProviderName: resource.schemaName,
    root,
    meta: queryMeta(facetsDocument(resource, options), facetsVariables(options)),
  };
}

export function deletePreviewRequest(
  resource: DataResourceMetadata,
  variables: DeletePreviewVariables,
): CustomGraphQLMutationRequest {
  const root = requiredRoot(resource, "deletePreview");
  return {
    dataProviderName: resource.schemaName,
    root,
    meta: mutationMeta(
      deletePreviewDocument(resource),
      {
        id: variables.id,
        confirm: variables.confirm ?? false,
      },
    ),
  };
}

export function revisionsRequest(
  resource: DataResourceMetadata,
  id: string,
): CustomGraphQLRequest {
  const root = requiredRoot(resource, "revisions");
  return {
    dataProviderName: resource.schemaName,
    root,
    meta: queryMeta(revisionsDocument(resource), { id }),
  };
}

function queryMeta(
  gqlQuery: DocumentNode,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlQuery, gqlVariables } as unknown as MetaQuery;
}

function mutationMeta(
  gqlMutation: DocumentNode,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlMutation, gqlVariables } as unknown as MetaQuery;
}

export function aggregateDocument(
  resource: DataResourceMetadata,
  options: {
    withWhere?: boolean;
    measures?: readonly AggregateMeasure[];
  } = {},
): DocumentNode {
  const root = requiredRoot(resource, "aggregate");
  const declarations = options.withWhere
    ? [`$where: ${requiredType(resource, "filter")}`]
    : [];
  const args = options.withWhere ? ["where: $where"] : [];
  return parse(
    `query ${operationName(root)}${variableBlock(declarations)} { ` +
      `${root}${argumentBlock(args)} { aggregate { ` +
      `${aggregateSelection(measuresFor(resource, options.measures))} } } }`,
  );
}

export function listDocument(
  resource: DataResourceMetadata,
  options: {
    fields: readonly string[];
    withWhere?: boolean;
    withOrderBy?: boolean;
  },
): DocumentNode {
  const root = requiredRoot(resource, "list");
  const aggregateRoot = requiredRoot(resource, "aggregate");
  const aggregateAlias = assertName(`${root}_aggregate`);
  const aggregateField = aggregateRoot === aggregateAlias
    ? aggregateRoot
    : `${aggregateAlias}: ${aggregateRoot}`;
  const declarations = ["$limit: Int", "$offset: Int"];
  const args = ["limit: $limit", "offset: $offset"];
  const aggregateArgs: string[] = [];
  if (options.withWhere) {
    declarations.push(`$where: ${requiredType(resource, "filter")}`);
    args.push("where: $where");
    aggregateArgs.push("where: $where");
  }
  if (options.withOrderBy) {
    declarations.push(`$order_by: [${requiredType(resource, "order")}!]`);
    args.push("order_by: $order_by");
  }
  return parse(
    `query ${operationName(`${root}_list`)}${variableBlock(declarations)} { ` +
      `${root}${argumentBlock(args)} { ${listSelection(options.fields)} } ` +
      `${aggregateField}${argumentBlock(aggregateArgs)} { aggregate { count } } }`,
  );
}

export function groupByDocument(
  resource: DataResourceMetadata,
  options: {
    dimensions: readonly GroupDimension[];
    withWhere?: boolean;
    withPagination?: boolean;
    measures?: readonly AggregateMeasure[];
  } = { dimensions: [] },
): DocumentNode {
  const root = requiredRoot(resource, "groups");
  const groupByType = requiredType(resource, "groupBySpec");
  const declarations = [`$group_by: [${groupByType}!]!`];
  const args = ["group_by: $group_by"];
  if (options.withWhere) {
    declarations.push(`$where: ${requiredType(resource, "filter")}`);
    args.push("where: $where");
  }
  if (options.withPagination) {
    declarations.push("$limit: Int", "$offset: Int");
    args.push("limit: $limit", "offset: $offset");
  }
  return parse(
    `query ${operationName(root)}${variableBlock(declarations)} { ` +
      `${root}${argumentBlock(args)} { ${groupSelection(
        measuresFor(resource, options.measures),
        options.dimensions,
      )} } }`,
  );
}

export function facetsDocument(
  resource: DataResourceMetadata,
  options: FacetsRequestOptions,
): DocumentNode {
  const root = requiredRoot(resource, "groups");
  if (options.facets.length === 0) {
    return parse(`query ${operationName(`${root}_facets`)} { __typename }`);
  }
  const groupByType = requiredType(resource, "groupBySpec");
  const declarations = options.facets.flatMap((facet, index) => {
    const variables = [`$group_by${index}: [${groupByType}!]!`];
    if (facet.where !== undefined) {
      variables.push(`$where${index}: ${requiredType(resource, "filter")}`);
    }
    if (facet.pageSize !== undefined) {
      variables.push(`$limit${index}: Int`, `$offset${index}: Int`);
    }
    return variables;
  });
  const fields = options.facets.map((facet, index) => {
    const args = [`group_by: $group_by${index}`];
    if (facet.where !== undefined) args.push(`where: $where${index}`);
    if (facet.pageSize !== undefined) {
      args.push(`limit: $limit${index}`, `offset: $offset${index}`);
    }
    return (
      `facet${index}: ${root}${argumentBlock(args)} { ` +
      `${groupSelection(measuresFor(resource, facet.measures), facet.dimensions)} }`
    );
  });
  return parse(
    `query ${operationName(`${root}_facets`)}${variableBlock(declarations)} { ` +
      `${fields.join(" ")} }`,
  );
}

export function deletePreviewDocument(
  resource: DataResourceMetadata,
): DocumentNode {
  const root = requiredRoot(resource, "deletePreview");
  return parse(
    `mutation ${operationName(root)}($id: ID!, $confirm: Boolean) { ` +
      `${root}(id: $id, confirm: $confirm) { ${DELETE_PREVIEW_SELECTION} } }`,
  );
}

export function revisionsDocument(resource: DataResourceMetadata): DocumentNode {
  const root = requiredRoot(resource, "revisions");
  const fields = revisionSelection(resource);
  return parse(
    `query ${operationName(root)}($id: ID!) { ` +
      `${root}(id: $id) { ${fields} } }`,
  );
}

export function groupByVariables(
  options: GroupByRequestOptions,
): Record<string, unknown> {
  return {
    group_by: options.dimensions.map(groupBySpecVariable),
    ...(options.where !== undefined ? { where: options.where } : {}),
    ...paginationVariables(options.page, options.pageSize),
  };
}

export function listVariables(
  options: ListRequestOptions,
): Record<string, unknown> {
  return {
    ...(options.where !== undefined ? { where: options.where } : {}),
    ...(options.orderBy !== undefined ? { order_by: options.orderBy } : {}),
  };
}

export function facetsVariables(
  options: FacetsRequestOptions,
): Record<string, unknown> {
  const variables: Record<string, unknown> = {};
  options.facets.forEach((facet, index) => {
    variables[`group_by${index}`] = facet.dimensions.map(groupBySpecVariable);
    if (facet.where !== undefined) variables[`where${index}`] = facet.where;
    Object.assign(
      variables,
      indexedPaginationVariables(index, facet.page, facet.pageSize),
    );
  });
  return variables;
}

export function extractAggregate(
  data: unknown,
  root: string,
): AggregateBucket | null {
  const node = fieldRecord(data, root);
  const aggregate = recordValue(node?.aggregate);
  if (!aggregate) return null;
  return { key: null, count: countOf(aggregate.count), ...extractMeasures(aggregate) };
}

export function extractGroupBy(data: unknown, root: string): GroupByResult {
  const rows = arrayValue(recordValue(data)?.[root]);
  const buckets = rows.filter(isRecord).map(groupBucket);
  return {
    count: buckets.reduce((total, bucket) => total + bucket.count, 0),
    buckets,
  };
}

export function extractFacets(
  data: unknown,
  facets: readonly FacetRequestSpec[],
): Readonly<Record<string, ResourceFacetResult>> {
  return Object.fromEntries(
    facets.map((facet, index) => [
      facet.id,
      facetResult(extractGroupBy(data, `facet${index}`), facet),
    ]),
  );
}

export function extractDeletePreview(data: unknown, root: string): DeletePreview | null {
  const preview = fieldRecord(data, root);
  if (!preview) return null;
  const previewRoot = deletePreviewNode(preview.root);
  if (
    typeof preview.totalDeletedCount !== "number" ||
    typeof preview.hasBlockers !== "boolean" ||
    previewRoot === null
  ) {
    return null;
  }
  return {
    totalDeletedCount: preview.totalDeletedCount,
    deleted: deletePreviewGroups(preview.deleted),
    updated: deletePreviewGroups(preview.updated),
    blocked: deletePreviewGroups(preview.blocked),
    hasBlockers: preview.hasBlockers,
    root: previewRoot,
  };
}

export function extractRevisions(
  data: unknown,
  root: string,
): readonly ResourceRevision[] {
  return arrayValue(recordValue(data)?.[root]).flatMap((row) => {
    if (!isRecord(row) || typeof row.id !== "string") return [];
    return [{
      ...row,
      id: row.id,
      createdAt: typeof row.createdAt === "string" ? row.createdAt : "",
      comment: typeof row.comment === "string" ? row.comment : null,
    }];
  });
}

const REVISION_META_FIELDS = new Set(["id", "createdAt", "comment", "__typename"]);

export function revisionSnapshot(revision: ResourceRevision): unknown {
  for (const [field, value] of Object.entries(revision)) {
    if (!REVISION_META_FIELDS.has(field) && value != null) return value;
  }
  return "";
}

export function groupDimension(
  input: string,
  key: string = input,
  options: Pick<GroupDimension, "granularity" | "rangeKey"> = {},
): GroupDimension {
  return { input, key, ...options };
}

const DELETE_PREVIEW_SELECTION =
  "totalDeletedCount hasBlockers " +
  "deleted { label count } updated { label count } blocked { label count } " +
  "root { label objectLabel objectId " +
  "children { label objectLabel objectId " +
  "children { label objectLabel objectId } } }";

function groupSelection(
  measures: readonly AggregateMeasure[],
  dimensions: readonly GroupDimension[],
): string {
  return (
    `key { ${groupKeySelection(dimensions)} } aggregate { ` +
    `${aggregateSelection(measures)} }`
  );
}

function groupKeySelection(dimensions: readonly GroupDimension[]): string {
  const fields = new Set<string>();
  for (const dimension of dimensions) {
    fields.add(assertName(dimension.key ?? dimension.input));
    if (dimension.rangeKey) fields.add(`${assertName(dimension.rangeKey)} { from to }`);
  }
  if (fields.size === 0) {
    throw new Error("Grouped requests require at least one key dimension.");
  }
  return [...fields].join(" ");
}

function aggregateSelection(measures: readonly AggregateMeasure[]): string {
  const fieldsByOp = new Map<
    (typeof AGGREGATE_MEASURE_OPERATORS)[number],
    string[]
  >();
  for (const measure of measures) {
    if (measure.op === "count") continue;
    if (!isAggregateOperator(measure.op)) {
      throw new Error(`Unsupported aggregate measure op: ${measure.op}`);
    }
    const field = assertName(measure.input ?? measure.field ?? "");
    const fields = fieldsByOp.get(measure.op) ?? [];
    if (!fields.includes(field)) fields.push(field);
    fieldsByOp.set(measure.op, fields);
  }
  return [
    "count",
    ...AGGREGATE_MEASURE_OPERATORS.flatMap((op) => {
      const fields = fieldsByOp.get(op);
      return fields && fields.length > 0 ? [`${op} { ${fields.join(" ")} }`] : [];
    }),
  ].join(" ");
}

function measuresFor(
  resource: DataResourceMetadata,
  measures: readonly AggregateMeasure[] | undefined,
): readonly AggregateMeasure[] {
  return measures ?? aggregateMeasuresFromMetadata(resource.defaultMeasures);
}

function aggregateMeasuresFromMetadata(
  measures: readonly DataResourceAggregateMeasureMetadata[] | undefined,
): readonly AggregateMeasure[] {
  return (measures ?? []).map((measure) => ({
    op: measure.op as AggregateMeasureOperator,
    field: measure.field,
    input: measure.input,
  }));
}

function facetResult(
  result: GroupByResult,
  facet: FacetRequestSpec,
): ResourceFacetResult {
  return {
    count: result.count,
    ...(result.totalCount === undefined ? {} : { totalCount: result.totalCount }),
    options: result.buckets.flatMap((bucket) => {
      const key = bucket.key ?? {};
      const valueKey = facet.valueKey ?? facet.dimensions[0]?.key ?? facet.dimensions[0]?.input;
      const value = valueKey ? stringValue(key[valueKey]) : null;
      if (value === null) return [];
      const labelKey = facet.labelKey ?? valueKey;
      const label = labelKey ? stringValue(key[labelKey]) ?? value : value;
      return [{ value, label, count: bucket.count, key }];
    }),
  };
}

function deletePreviewGroups(value: unknown): DeletePreviewGroup[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((group) =>
    isRecord(group) && typeof group.label === "string" && typeof group.count === "number"
      ? [{ label: group.label, count: group.count }]
      : [],
  );
}

function deletePreviewNode(value: unknown): DeletePreviewNode | null {
  if (
    !isRecord(value) ||
    typeof value.label !== "string" ||
    typeof value.objectLabel !== "string" ||
    (value.objectId !== null &&
      value.objectId !== undefined &&
      typeof value.objectId !== "string")
  ) {
    return null;
  }
  return {
    label: value.label,
    objectLabel: value.objectLabel,
    objectId: value.objectId ?? null,
    children: Array.isArray(value.children)
      ? value.children.flatMap((child) => {
          const node = deletePreviewNode(child);
          return node ? [node] : [];
        })
      : [],
  };
}

function revisionSelection(resource: DataResourceMetadata): string {
  const fields = resource.revisionFields ?? [];
  if (fields.length === 0) {
    throw new Error(
      `Resource "${resource.modelLabel}" does not declare revision fields.`,
    );
  }
  return fields.map(assertName).join(" ");
}

function listSelection(fields: readonly string[]): string {
  return printSelection(buildSelection(fields));
}

function groupBucket(group: Record<string, unknown>): AggregateBucket {
  const aggregate = recordValue(group.aggregate) ?? {};
  return {
    key: recordValue(group.key) ?? {},
    count: countOf(aggregate.count),
    ...extractMeasures(aggregate),
  };
}

function groupBySpecVariable(dimension: GroupDimension): Record<string, string> {
  return {
    field: dimension.input,
    ...(dimension.granularity ? { granularity: dimension.granularity } : {}),
  };
}

function extractMeasures(
  source: Record<string, unknown>,
): Partial<
  Record<(typeof AGGREGATE_MEASURE_OPERATORS)[number], AggregateMeasureValues>
> {
  const measures: Partial<
    Record<(typeof AGGREGATE_MEASURE_OPERATORS)[number], AggregateMeasureValues>
  > = {};
  for (const op of AGGREGATE_MEASURE_OPERATORS) {
    const values = recordValue(source[op]);
    if (values) measures[op] = values;
  }
  return measures;
}

function paginationVariables(
  page: number | undefined,
  pageSize: number | undefined,
): Record<string, number> {
  if (pageSize === undefined) return {};
  const limit = clampPageSize(pageSize);
  return {
    limit,
    offset: (normalisePage(page) - 1) * limit,
  };
}

function indexedPaginationVariables(
  index: number,
  page: number | undefined,
  pageSize: number | undefined,
): Record<string, number> {
  const pagination = paginationVariables(page, pageSize);
  return Object.fromEntries(
    Object.entries(pagination).map(([key, value]) => [`${key}${index}`, value]),
  );
}

function variableBlock(declarations: readonly string[]): string {
  return declarations.length > 0 ? `(${declarations.join(", ")})` : "";
}

function argumentBlock(args: readonly string[]): string {
  return args.length > 0 ? `(${args.join(", ")})` : "";
}

function requiredRoot(
  resource: DataResourceMetadata,
  root: keyof DataResourceMetadata["roots"],
): string {
  const value = resource.roots[root];
  if (!value) {
    throw new Error(`Resource "${resource.modelLabel}" does not expose ${root}.`);
  }
  return assertName(value);
}

function requiredType(
  resource: DataResourceMetadata,
  type: keyof DataResourceMetadata["typeNames"],
): string {
  const value = resource.typeNames[type];
  if (!value) {
    throw new Error(
      `Resource "${resource.modelLabel}" does not declare ${type} type metadata.`,
    );
  }
  return assertName(value);
}

function operationName(name: string): string {
  return assertName(name);
}

function assertName(name: string): string {
  if (!GRAPHQL_NAME.test(name)) {
    throw new Error(`Invalid GraphQL name: ${name}`);
  }
  return name;
}

function isAggregateOperator(
  op: AggregateMeasureOperator,
): op is (typeof AGGREGATE_MEASURE_OPERATORS)[number] {
  return (AGGREGATE_MEASURE_OPERATORS as readonly string[]).includes(op);
}

function normalisePage(page: number | undefined): number {
  return Math.max(1, Math.floor(page ?? 1));
}

export const MAX_PAGE_SIZE = 100;
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 80, MAX_PAGE_SIZE] as const;
export const DEFAULT_PAGE_SIZE = PAGE_SIZE_OPTIONS[2];

export function clampPageSize(pageSize: number): number {
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.floor(pageSize)));
}

function fieldRecord(data: unknown, field: string): Record<string, unknown> | null {
  return recordValue(recordValue(data)?.[field]);
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function arrayValue(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function countOf(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function stringValue(value: unknown): string | null {
  if (value == null) return null;
  const text = String(value).trim();
  return text === "" ? null : text;
}
