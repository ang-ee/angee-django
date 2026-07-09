import {
  isClientRowModel,
  type DataResourceGroupBucketFilterMetadata,
  type DataResourceGroupDimensionMetadata,
  type DataResourceGroupExtractionMetadata,
  type ModelMetadata,
} from "./artifact";
import { resourceFieldPathToSnake } from "./naming";

export interface ResourceGroupSpec {
  field: string;
  granularity?: string;
  aggregateField?: string;
  aggregateKey?: string;
}

export interface ResourceGroupBucket {
  key?: Readonly<Record<string, unknown>> | null;
}

export type ResourceBucketFilter = Record<string, unknown>;

export function groupDimensionForGroup(
  group: ResourceGroupSpec,
  metadata: ModelMetadata | null,
): DataResourceGroupDimensionMetadata {
  return groupDimensionForField(group.aggregateField ?? group.field, metadata);
}

export function groupDimensionForField(
  field: string,
  metadata: ModelMetadata | null,
): DataResourceGroupDimensionMetadata {
  const dimension = resourceGroupDimensionForField(field, metadata);
  if (!dimension) {
    const model = metadata?.typeName ?? "unknown model";
    throw new Error(
      `Resource metadata for ${model} does not declare group dimension "${field}".`,
    );
  }
  return dimension;
}

export function resourceGroupDimensionForField(
  field: string,
  metadata: ModelMetadata | null,
): DataResourceGroupDimensionMetadata | undefined {
  const dimensions = metadata?.resource?.groupDimensions ?? [];
  const snakeField = resourceFieldPathToSnake(field);
  return dimensions.find((candidate) =>
    candidate.field === field ||
    candidate.key === field ||
    resourceFieldPathToSnake(candidate.field) === snakeField ||
    resourceFieldPathToSnake(candidate.key) === snakeField
  );
}

export function groupExtractionForGroup(
  dimension: DataResourceGroupDimensionMetadata,
  group: ResourceGroupSpec,
): DataResourceGroupExtractionMetadata | null {
  if (!group.granularity) return null;
  const requested = group.granularity.toUpperCase();
  const extraction = dimension.extractions?.find(
    (candidate) =>
      candidate.name === group.granularity ||
      candidate.input === requested,
  );
  if (!extraction) {
    throw new Error(
      `Resource metadata for group dimension "${dimension.field}" does not ` +
        `declare extraction "${group.granularity}".`,
    );
  }
  return extraction;
}

export function groupAllowedByResource(
  group: ResourceGroupSpec,
  metadata: ModelMetadata | null,
): boolean {
  if (isClientRowModel(metadata?.resource)) {
    return groupFieldAvailableOnResource(group.field, metadata);
  }
  const groupByFields = metadata?.resource?.groupByFields;
  if (!groupByFields) return true;
  if (group.aggregateField && !groupFieldAvailableOnResource(group.field, metadata)) {
    return false;
  }
  const aggregateField = group.aggregateField ?? group.field;
  const dimension =
    resourceGroupDimensionForField(aggregateField, metadata) ??
    resourceGroupDimensionForField(group.field, metadata);
  if (dimension) return groupByFields.includes(dimension.field);
  const aggregateSnake = resourceFieldPathToSnake(aggregateField);
  const fieldSnake = resourceFieldPathToSnake(group.field);
  return groupByFields.some((field) =>
    field === aggregateField ||
    field === group.field ||
    field === aggregateSnake ||
    field === fieldSnake ||
    resourceFieldPathToSnake(field) === aggregateSnake ||
    resourceFieldPathToSnake(field) === fieldSnake
  );
}

export function groupSupportedByResource(
  group: ResourceGroupSpec,
  metadata: ModelMetadata | null,
): boolean {
  if (!groupAllowedByResource(group, metadata)) return false;
  if (isClientRowModel(metadata?.resource)) return true;
  const dimensions = metadata?.resource?.groupDimensions;
  if (!dimensions) return true;
  const dimension =
    resourceGroupDimensionForField(group.aggregateField ?? group.field, metadata) ??
    resourceGroupDimensionForField(group.field, metadata);
  if (!dimension) return false;
  if (!group.granularity) return true;
  const requested = group.granularity.toUpperCase();
  return (dimension.extractions ?? []).some(
    (extraction) =>
      extraction.name === group.granularity || extraction.input === requested,
  );
}

export function groupFieldAvailableOnResource(
  field: string,
  metadata: ModelMetadata | null,
): boolean {
  const [head] = field.split(".");
  const fieldMetadata = metadata?.fields[field] ?? metadata?.fields[head ?? field];
  if (!fieldMetadata) return false;
  return fieldMetadata.kind !== "list";
}

export function bucketFilterForGroup(
  bucket: ResourceGroupBucket,
  group: ResourceGroupSpec | undefined,
  metadata: ModelMetadata | null,
): ResourceBucketFilter | undefined {
  if (!group) return {};
  const dimensionMetadata = groupDimensionForGroup(group, metadata);
  const extraction = groupExtractionForGroup(dimensionMetadata, group);
  const filter = extraction?.filter ?? dimensionMetadata.filter;
  if (!filter) {
    throw new Error(
      `Resource metadata for group dimension "${dimensionMetadata.field}" does ` +
        "not declare a bucket filter.",
    );
  }
  if (filter.kind === "range") {
    return bucketRangeDrillDownFilter(bucket, filter, dimensionMetadata);
  }

  const value = bucket.key?.[filter.valueKey ?? extraction?.key ?? dimensionMetadata.key];
  if (value === undefined) return undefined;

  if (isNullBucketValue(value, filter)) {
    return bucketNullFilter(filter);
  }

  return bucketEqualityFilter(filter, bucketFilterValue(value, filter));
}

function bucketRangeDrillDownFilter(
  bucket: ResourceGroupBucket,
  filter: DataResourceGroupBucketFilterMetadata,
  dimension: DataResourceGroupDimensionMetadata,
): ResourceBucketFilter | undefined {
  const rangeValue = filter.rangeKey ? bucket.key?.[filter.rangeKey] : undefined;
  const scalar = dateRangeScalar(dimension);
  const range = scalar ? bucketRangeFilter(rangeValue, scalar) : null;
  if (range) return { [filter.field]: range };

  const value = filter.valueKey ? bucket.key?.[filter.valueKey] : undefined;
  if (isNullBucketValue(value, filter)) return bucketNullFilter(filter);
  if (value === undefined) return undefined;
  throw new Error(
    `Group bucket for "${dimension.field}" did not include declared range key ` +
      `"${filter.rangeKey ?? ""}".`,
  );
}

function dateRangeScalar(
  dimension: DataResourceGroupDimensionMetadata,
): "Date" | "DateTime" | null {
  return dimension.scalar === "Date" || dimension.scalar === "DateTime"
    ? dimension.scalar
    : null;
}

function bucketEqualityFilter(
  filter: DataResourceGroupBucketFilterMetadata,
  value: unknown,
): ResourceBucketFilter {
  if (filter.lookup) return { [filter.field]: { [filter.lookup]: value } };
  return { [filter.field]: value };
}

function bucketFilterValue(
  value: unknown,
  filter: DataResourceGroupBucketFilterMetadata,
): unknown {
  const mapped = filter.valueMap?.find((item) => Object.is(item.from, value));
  if (mapped) return mapped.to;
  if (filter.valueTransform?.startsWith("jsonObject:")) {
    return jsonObjectValue(value, filter.valueTransform.slice("jsonObject:".length));
  }
  if (filter.valueTransform === "json") return jsonBucketValue(value);
  return value;
}

function isNullBucketValue(
  value: unknown,
  filter: DataResourceGroupBucketFilterMetadata,
): boolean {
  if (filter.valueTransform?.startsWith("jsonObject:")) return false;
  return value === null || (filter.kind === "range" && value === "");
}

function bucketNullFilter(
  filter: DataResourceGroupBucketFilterMetadata,
): ResourceBucketFilter {
  return { [filter.field]: { [filter.nullLookup ?? "isNull"]: true } };
}

function jsonBucketValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed || !/^[\[{"]|^(true|false|null|-?\d)/.test(trimmed)) {
    return value;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function jsonObjectValue(value: unknown, path: string): unknown {
  const keys = path.split(".").filter(Boolean);
  if (keys.length === 0) return value;
  let current: unknown = value;
  for (let index = keys.length - 1; index >= 0; index -= 1) {
    const key = keys[index];
    if (!key) continue;
    current = { [key]: current };
  }
  return current;
}

function bucketRangeFilter(
  value: unknown,
  scalar: "Date" | "DateTime",
): ResourceBucketFilter[string] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const range = value as Record<string, unknown>;
  const from = dateBucketBoundary(range.from, scalar);
  const to = dateBucketBoundary(range.to, scalar);
  return from && to ? { gte: from, lt: to } : null;
}

function dateBucketBoundary(
  value: unknown,
  scalar: "Date" | "DateTime",
): string | null {
  const date = dateBucketStart(value);
  return date ? formatDateBoundary(date, scalar) : null;
}

function dateBucketStart(value: unknown): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const normalized = normalizeDateBucketValue(trimmed);
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function normalizeDateBucketValue(value: string): string {
  const withTimeSeparator = value.replace(
    /^(\d{4}-\d{2}-\d{2})\s+(\d)/,
    "$1T$2",
  );
  if (/^\d{4}-\d{2}-\d{2}$/.test(withTimeSeparator)) {
    return `${withTimeSeparator}T00:00:00.000Z`;
  }
  if (
    /^\d{4}-\d{2}-\d{2}T/.test(withTimeSeparator) &&
    !/(Z|[+-]\d{2}:?\d{2})$/.test(withTimeSeparator)
  ) {
    return `${withTimeSeparator}Z`;
  }
  return withTimeSeparator;
}

function formatDateBoundary(date: Date, scalar: "Date" | "DateTime"): string {
  if (scalar === "Date") {
    return [
      date.getUTCFullYear(),
      String(date.getUTCMonth() + 1).padStart(2, "0"),
      String(date.getUTCDate()).padStart(2, "0"),
    ].join("-");
  }
  return date.toISOString();
}
