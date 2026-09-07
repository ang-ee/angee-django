import * as v from "valibot";

/** Public operators; dialect spellings are private to ResourceQuery.toWhere. */
export const FILTER_OPERATORS = [
  "exact", "ne", "gt", "gte", "lt", "lte", "inList", "notInList", "isNull",
  "contains", "iContains", "startsWith", "iStartsWith", "endsWith", "iEndsWith",
  "like", "iLike", "notLike", "notILike", "similar", "notSimilar",
  "regex", "iRegex", "notRegex", "notIRegex", "jsonContains", "jsonContainedIn",
  "hasKey", "hasKeysAny", "hasKeysAll",
] as const;
export type FilterOperator = (typeof FILTER_OPERATORS)[number];
const OptionalString = v.nullish(v.string());
const EnumValue = v.object({ value: v.string(), description: OptionalString });
const DrillSchema = v.object({
  kind: v.picklist(["value", "identity", "range", "json"]),
  field: v.string(),
  valueKey: v.string(),
  rangeKey: OptionalString,
  jsonPath: OptionalString,
  valueTransform: v.nullish(v.literal("json")),
  valueMap: v.optional(v.pipe(v.array(v.object({ from: v.unknown(), to: v.unknown() })), v.readonly()), []),
  nullMode: v.picklist(["isNull", "value", "unavailable"]),
});
const ExtractionSchema = v.object({
  name: v.string(), input: v.string(), key: v.string(),
  rangeKey: OptionalString, drill: v.nullish(DrillSchema),
});
const QueryFieldSchema = v.object({
  kind: v.picklist(["scalar", "enum", "relation", "json", "list", "object"]),
  scalar: OptionalString,
  values: v.optional(v.pipe(v.array(EnumValue), v.readonly()), []),
  nullable: v.optional(v.boolean(), true),
  row: v.nullish(v.object({ path: v.string(), paths: v.pipe(v.array(v.string()), v.readonly()) })),
  filter: v.nullish(v.object({
    field: v.string(), scalar: v.string(),
    values: v.optional(v.pipe(v.array(EnumValue), v.readonly()), []),
    valueMap: v.optional(v.pipe(v.array(v.object({ from: v.unknown(), to: v.unknown() })), v.readonly())),
    operators: v.pipe(v.array(v.picklist(FILTER_OPERATORS)), v.readonly()),
  })),
  sort: v.nullish(v.object({ field: v.string() })),
  relation: v.nullish(v.object({ model: v.string(), identityPath: OptionalString, labelPath: OptionalString })),
});
const AxisSchema = v.object({
  field: v.string(),
  kind: v.picklist(["column", "relation", "json", "date"]),
  identityPath: OptionalString,
  paths: v.pipe(v.array(v.string()), v.readonly()),
  labelPath: OptionalString,
  server: v.nullish(v.object({
    input: v.string(), key: v.string(), labelInput: OptionalString, labelKey: OptionalString,
  })),
  extractions: v.optional(v.pipe(v.array(ExtractionSchema), v.readonly()), []),
  drill: v.nullish(DrillSchema),
});
export const GroupSpecSchema = v.strictObject({ field: v.string(), granularity: v.optional(v.string()) });
export const GroupSpecsSchema = v.array(GroupSpecSchema);
export const QuerySortSchema = v.strictObject({ field: v.string(), direction: v.picklist(["ASC", "DESC"]) });
/** The only generated query contract: executable fields, axes and identity. */
export const DataResourceQuerySchema = v.object({
  identity: v.object({ field: v.string() }),
  fields: v.record(v.string(), QueryFieldSchema),
  axes: v.record(v.string(), AxisSchema),
  sort: v.object({ default: v.pipe(v.array(QuerySortSchema), v.readonly()) }),
  paging: v.object({ maxPageSize: v.nullish(v.number()), maxGroups: v.nullish(v.number()) }),
});
export type DataResourceQuery = v.InferOutput<typeof DataResourceQuerySchema>;
export type QueryField = v.InferOutput<typeof QueryFieldSchema>;
export type QueryAxis = v.InferOutput<typeof AxisSchema>;
export type QueryDrill = v.InferOutput<typeof DrillSchema>;
export type QueryExtraction = v.InferOutput<typeof ExtractionSchema>;
export type GroupSpec = v.InferOutput<typeof GroupSpecSchema>;
export type QuerySort = v.InferOutput<typeof QuerySortSchema>;
