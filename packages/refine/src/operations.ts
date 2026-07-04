import {
  arrayValue,
  countOf,
  fieldRecord,
  isRecord,
  mutationMeta,
  operationName,
  queryMeta,
  recordValue,
  stringValue,
} from "./dialect/wire";
import type { MetaQuery } from "@refinedev/core";

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

export type GroupOrderDirection = "ASC" | "DESC";
export type GroupNullsPosition = "FIRST" | "LAST";

export interface GroupOrder {
  /** Typed key field used by `<resource>_groups(order_by: ...)`. */
  field: string;
  direction?: GroupOrderDirection;
  nulls?: GroupNullsPosition | null;
}

export interface AggregateRequestOptions {
  measures?: readonly AggregateMeasure[];
  where?: Record<string, unknown>;
}

export interface GroupByRequestOptions extends AggregateRequestOptions {
  dimensions: readonly GroupDimension[];
  orderBy?: readonly GroupOrder[];
  page?: number;
  pageSize?: number;
}

export interface FacetRequestSpec extends GroupByRequestOptions {
  id: string;
  valueKey?: string;
  labelKey?: string;
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

/** The shape every backend single-id ActionResult mutation returns. */
export interface ActionOutcome {
  ok: boolean;
  message: string;
}

/** Variables for a mutation shaped `<field>(id: ID!): ActionResult`. */
export interface ByIdVariables extends Record<string, unknown> {
  id: string;
}

export interface ResourceRevision extends Record<string, unknown> {
  id: string;
  created_at: string;
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

export interface CustomGraphQLOperationTarget {
  dataProviderName: string;
  root: string;
}

export function aggregateRequest(
  target: CustomGraphQLOperationTarget,
  options: AggregateRequestOptions = {},
  requestOptions: { document: unknown },
): CustomGraphQLRequest {
  const operation = operationTarget(target);
  const withWhere = options.where !== undefined;
  return {
    dataProviderName: operation.dataProviderName,
    root: operation.root,
    meta: queryMeta(
      requestOptions.document,
      withWhere ? { where: options.where } : {},
    ),
  };
}

export function groupByRequest(
  target: CustomGraphQLOperationTarget,
  options: GroupByRequestOptions,
  requestOptions: { document: unknown },
): CustomGraphQLRequest {
  const operation = operationTarget(target);
  return {
    dataProviderName: operation.dataProviderName,
    root: operation.root,
    meta: queryMeta(requestOptions.document, groupByVariables(options)),
  };
}

export function deletePreviewRequest(
  target: CustomGraphQLOperationTarget,
  variables: DeletePreviewVariables,
  options: { document: unknown },
): CustomGraphQLMutationRequest {
  const operation = operationTarget(target);
  return {
    dataProviderName: operation.dataProviderName,
    root: operation.root,
    meta: mutationMeta(
      options.document,
      {
        id: variables.id,
        confirm: variables.confirm ?? false,
      },
    ),
  };
}

/** One editable line submitted to the authored `<resource>_save` mutation. */
export interface LineInput extends Record<string, unknown> {
  /** Present for an existing line (update); absent for a new line (create). */
  id?: string;
}

/** Variables for the authored `<resource>_save(pk, patch, lines)` diff-apply mutation (F6). */
export interface ResourceSaveVariables extends Record<string, unknown> {
  pk: string;
  patch?: Record<string, unknown>;
  lines?: readonly LineInput[];
}

/**
 * Build the request for the authored `<resource>_save(pk, patch, lines)` diff-apply
 * mutation (F6) — the transactional parent-patch-plus-child-upsert/delete write. The
 * frozen backend contract (spec §3.14): `lines` is the full desired child list, each
 * carrying its public `id` to update and omitting it to create; a stored child whose
 * id is absent from the list is deleted; `patch` is the parent field patch.
 *
 * This is the single point where the frontend binds to that backend operation. The
 * generated typed document for the resource's `save` root is passed as `document`
 * (resolved from the operation registry or authored in the addon) — like every other
 * authored dialect operation here, this builder stays metadata-free.
 */
export function saveRequest(
  target: CustomGraphQLOperationTarget,
  variables: ResourceSaveVariables,
  options: { document: unknown },
): CustomGraphQLMutationRequest {
  const operation = operationTarget(target);
  return {
    dataProviderName: operation.dataProviderName,
    root: operation.root,
    meta: mutationMeta(options.document, {
      pk: variables.pk,
      ...(variables.patch !== undefined ? { patch: variables.patch } : {}),
      ...(variables.lines !== undefined ? { lines: variables.lines } : {}),
    }),
  };
}

/** Pull the saved parent row (with its returned lines) from a `<resource>_save` response. */
export function extractSaveResult(
  data: unknown,
  root: string,
): Record<string, unknown> | null {
  return fieldRecord(data, root);
}

export function actionRequest(
  field: string,
  variables: ByIdVariables,
  options: { dataProviderName?: string; document: unknown },
): CustomGraphQLMutationRequest {
  const root = operationName(field);
  return {
    dataProviderName: options.dataProviderName ?? "default",
    root,
    meta: mutationMeta(options.document, variables),
  };
}

export function revisionsRequest(
  target: CustomGraphQLOperationTarget,
  id: string,
  options: { document: unknown },
): CustomGraphQLRequest {
  const operation = operationTarget(target);
  return {
    dataProviderName: operation.dataProviderName,
    root: operation.root,
    meta: queryMeta(options.document, { id }),
  };
}

export function groupByVariables(
  options: GroupByRequestOptions,
): Record<string, unknown> {
  return {
    group_by: options.dimensions.map(groupBySpecVariable),
    ...(options.where !== undefined ? { where: options.where } : {}),
    ...(options.orderBy !== undefined
      ? { order_by: options.orderBy.map(groupOrderVariable) }
      : {}),
    ...paginationVariables(options.page, options.pageSize),
  };
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

export function extractFacet(
  data: unknown,
  root: string,
  facet: FacetRequestSpec,
): ResourceFacetResult {
  return facetResult(extractGroupBy(data, root), facet);
}

// The wire payload is snake_case (the schema's Hasura naming); this boundary
// reads those keys and exposes the idiomatic camelCase `DeletePreview` domain
// shape the views consume.
export function extractDeletePreview(data: unknown, root: string): DeletePreview | null {
  const preview = fieldRecord(data, root);
  if (!preview) return null;
  const previewRoot = deletePreviewNode(preview.root);
  if (
    typeof preview.total_deleted_count !== "number" ||
    typeof preview.has_blockers !== "boolean" ||
    previewRoot === null
  ) {
    return null;
  }
  return {
    totalDeletedCount: preview.total_deleted_count,
    deleted: deletePreviewGroups(preview.deleted),
    updated: deletePreviewGroups(preview.updated),
    blocked: deletePreviewGroups(preview.blocked),
    hasBlockers: preview.has_blockers,
    root: previewRoot,
  };
}

export function extractActionOutcome(
  data: unknown,
  root: string,
): ActionOutcome | null {
  const outcome = fieldRecord(data, root);
  if (!outcome || typeof outcome.ok !== "boolean") return null;
  return {
    ok: outcome.ok,
    message: typeof outcome.message === "string" ? outcome.message : "",
  };
}

/**
 * Normalize a backend ActionResult into the rendered action contract: a business
 * failure throws, a success returns the success message, and a missing outcome
 * stays silent because the mutation transport already reported the failure.
 */
export function runActionResult(
  outcome: ActionOutcome | null | undefined,
): string | undefined {
  if (!outcome) return undefined;
  if (!outcome.ok) throw new Error(outcome.message);
  return outcome.message;
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
      created_at: typeof row.created_at === "string" ? row.created_at : "",
      comment: typeof row.comment === "string" ? row.comment : null,
    }];
  });
}

const REVISION_META_FIELDS = new Set(["id", "created_at", "comment", "__typename"]);

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
    typeof value.object_label !== "string" ||
    (value.object_id !== null &&
      value.object_id !== undefined &&
      typeof value.object_id !== "string")
  ) {
    return null;
  }
  return {
    label: value.label,
    objectLabel: value.object_label,
    objectId: value.object_id ?? null,
    children: Array.isArray(value.children)
      ? value.children.flatMap((child) => {
          const node = deletePreviewNode(child);
          return node ? [node] : [];
        })
      : [],
  };
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

function groupOrderVariable(order: GroupOrder): Record<string, unknown> {
  return {
    field: order.field,
    ...(order.direction ? { direction: order.direction } : {}),
    ...(order.nulls !== undefined ? { nulls: order.nulls } : {}),
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

function operationTarget(
  target: CustomGraphQLOperationTarget,
): CustomGraphQLOperationTarget {
  return {
    dataProviderName: target.dataProviderName,
    root: operationName(target.root),
  };
}

function normalisePage(page: number | undefined): number {
  return Math.max(1, Math.floor(page ?? 1));
}

export const MAX_PAGE_SIZE = 100;

export function clampPageSize(pageSize: number): number {
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.floor(pageSize)));
}
