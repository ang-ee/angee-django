import * as v from "valibot";
import { Filter, filterValueFromUnknown as jsonValue, isFilterObject as isRecord } from "./filter.js";
import { QueryParseError } from "./query-error.js";
export { QueryParseError } from "./query-error.js";
import type { DataResourceMetadata, ModelMetadata } from "./artifact.js";
import {
  DataResourceQuerySchema, FILTER_OPERATORS, GroupSpecsSchema,
  type DataResourceQuery, type FilterOperator, type GroupSpec, type QueryAxis,
  type QueryExtraction, type QueryField, type QuerySort,
} from "./query-schema.js";

export type FilterPrimitive = string | number | boolean | null;
export type FilterValue = FilterPrimitive | readonly FilterValue[] | { readonly [key: string]: FilterValue };
export type QueryFilter = { readonly [field: string]: FilterValue };
export type FilterRecord = QueryFilter;
export interface GroupBucket { key?: Readonly<Record<string, unknown>> | null }
export interface GroupDimension { input: string; key: string; granularity?: string; rangeKey?: string }
export interface GroupProjection {
  dimensions: readonly GroupDimension[];
  orderBy: readonly { field: string; direction: "ASC"; nulls: "LAST" }[];
  valueKey: string;
  labelKey?: string;
}

const TEXT_OPERATORS: readonly FilterOperator[] = [
  "contains", "iContains", "startsWith", "iStartsWith", "endsWith", "iEndsWith",
  "like", "iLike", "notLike", "notILike", "similar", "notSimilar", "regex", "iRegex", "notRegex", "notIRegex",
];
const BASIC_OPERATORS: readonly FilterOperator[] = ["exact", "ne", "inList", "notInList", "isNull"];
const ORDER_OPERATORS: readonly FilterOperator[] = ["gt", "gte", "lt", "lte"];
const WIRE_OPERATORS: Readonly<Record<FilterOperator, string>> = {
  exact: "_eq", ne: "_neq", gt: "_gt", gte: "_gte", lt: "_lt", lte: "_lte",
  inList: "_in", notInList: "_nin", isNull: "_is_null",
  contains: "_like", iContains: "_ilike", startsWith: "_like", iStartsWith: "_ilike",
  endsWith: "_like", iEndsWith: "_ilike", like: "_like", iLike: "_ilike",
  notLike: "_nlike", notILike: "_nilike", similar: "_similar", notSimilar: "_nsimilar",
  regex: "_regex", iRegex: "_iregex", notRegex: "_nregex", notIRegex: "_niregex",
  jsonContains: "_contains", jsonContainedIn: "_contained_in", hasKey: "_has_key",
  hasKeysAny: "_has_keys_any", hasKeysAll: "_has_keys_all",
};

export interface LocalQueryField {
  kind?: QueryField["kind"];
  scalar?: string;
  values?: QueryField["values"];
  nullable?: boolean;
  identityPath?: string;
  labelPath?: string;
}

/**
 * One resource's executable query language. Generated metadata owns capability
 * and wire facts; this object validates intent and projects it to each adapter.
 * It knows neither generated project types nor table/rendering libraries.
 */
export class ResourceQuery {
  private static readonly cache = new WeakMap<DataResourceMetadata, ResourceQuery>();
  private constructor(readonly contract: DataResourceQuery) {}

  /** Authored server projections declare the same language as generated resources. */
  static fromContract(contract: DataResourceQuery): ResourceQuery {
    return new ResourceQuery(parse(DataResourceQuerySchema, contract, "query"));
  }

  static from(resource: DataResourceMetadata | ModelMetadata): ResourceQuery {
    const data = "resource" in resource ? resource.resource as DataResourceMetadata : resource;
    const cached = ResourceQuery.cache.get(data);
    if (cached) return cached;
    const query = new ResourceQuery(parse(DataResourceQuerySchema, data.query, "query"));
    ResourceQuery.cache.set(data, query);
    return query;
  }

  /** Explicit local column declarations are a query source, without server axes. */
  static forRows(options: { fields: Readonly<Record<string, LocalQueryField>>; identityField?: string }): ResourceQuery {
    const fields: Record<string, QueryField> = {};
    const axes: Record<string, QueryAxis> = {};
    for (const [name, declaration] of Object.entries(options.fields)) {
      const kind = declaration.kind ?? "scalar";
      const operators = [...BASIC_OPERATORS];
      if (kind === "json" || declaration.scalar === "JSON") operators.push("jsonContains");
      if (kind === "scalar" && declaration.scalar !== "JSON") {
        operators.push(...ORDER_OPERATORS);
        if (declaration.scalar === "String" || declaration.scalar == null) {
          operators.push(...TEXT_OPERATORS.filter((operator) => !["similar", "notSimilar", "regex", "iRegex", "notRegex", "notIRegex"].includes(operator)));
        }
      }
      fields[name] = {
        kind, row: { path: declaration.identityPath ?? name, paths: [declaration.identityPath ?? name] }, scalar: declaration.scalar, values: declaration.values ?? [], nullable: declaration.nullable ?? true,
        filter: { field: name, operators, scalar: kind === "enum" ? "Enum" : kind === "relation" ? "ID" : kind === "json" ? "JSON" : declaration.scalar ?? "Unknown", values: declaration.values ?? [] }, sort: { field: name },
      };
      if (kind === "list" || kind === "object") continue;
      const date = declaration.scalar === "Date" || declaration.scalar === "DateTime";
      axes[name] = {
        field: name, kind: kind === "relation" ? "relation" : kind === "json" ? "json" : date ? "date" : "column",
        identityPath: declaration.identityPath ?? name, labelPath: declaration.labelPath,
        paths: [...new Set([declaration.identityPath ?? name, ...(declaration.labelPath ? [declaration.labelPath] : [])])],
        extractions: date ? ["year", "quarter", "month", "week", "day", "hour", "minute", "second"].map((value) => ({ name: value, input: value.toUpperCase(), key: value })) : [],
      };
    }
    return new ResourceQuery({ identity: { field: options.identityField ?? "id" }, fields, axes, sort: { default: [] } });
  }

  get fields(): DataResourceQuery["fields"] { return this.contract.fields; }
  get axes(): DataResourceQuery["axes"] { return this.contract.axes; }

  filterFrom(value: unknown = {}): QueryFilter {
    return this.parseFilter(Filter.from(value).value, "filter");
  }

  groupsFrom(value: unknown = []): GroupAxis[] {
    const specs = parse(GroupSpecsSchema, value, "groups");
    const axes = specs.map((spec) => this.group(spec));
    const seen = new Set<string>();
    for (const axis of axes) {
      if (seen.has(axis.id)) throw new QueryParseError("groups", `duplicate group "${axis.id}"`);
      seen.add(axis.id);
    }
    return axes;
  }

  axis(field: string, granularity?: string): GroupAxis {
    const canonical = this.canonicalAxisField(field);
    const declaration = canonical === undefined ? undefined : this.contract.axes[canonical];
    if (canonical === undefined || !declaration) throw new QueryParseError(`groups.${field}`, "unknown group axis");
    return new GroupAxis(this, declaration, { field: canonical, ...(granularity ? { granularity } : {}) });
  }

  /** Resolve a declared axis or its unique identity path; labels never identify axes. */
  canonicalAxisField(field: string): string | undefined {
    if (this.contract.axes[field]) return field;
    const matches = Object.entries(this.contract.axes).filter(([, axis]) => axis.identityPath === field);
    if (matches.length > 1) throw new QueryParseError(`groups.${field}`, "ambiguous group identity path");
    return matches[0]?.[0];
  }

  group(spec: GroupSpec): GroupAxis { return this.axis(spec.field, spec.granularity); }

  relation(field: string): QueryField["relation"] { return this.fields[field]?.relation; }

  selection(groups: readonly (GroupSpec | GroupAxis)[]): string[] {
    return [...new Set(groups.flatMap((group) => this.resolveAxis(group).selection))];
  }

  toGroupBy(groups: readonly (GroupSpec | GroupAxis)[]): GroupProjection {
    if (groups.length === 0) throw new QueryParseError("groups", "at least one group is required");
    const projections = groups.map((group) => this.resolveAxis(group).groupBy());
    const last = projections[projections.length - 1]!;
    return { ...last, dimensions: projections.flatMap((projection) => projection.dimensions) };
  }

  drill(group: GroupSpec | GroupAxis, bucket: GroupBucket): QueryFilter | undefined {
    return this.resolveAxis(group).drill(bucket);
  }

  /** Parse every input, preserving empty membership/boolean branches and null comparisons. */
  toWhere(...values: readonly unknown[]): Record<string, unknown> {
    const parts = values.map((value) => this.where(this.filterFrom(value))).filter((value) => Object.keys(value).length > 0);
    return parts.length === 0 ? {} : parts.length === 1 ? parts[0]! : { _and: parts };
  }

  /** Remove facet constraints while retaining the logic of every other branch. */
  withoutFields(value: unknown, fields: Iterable<string>): QueryFilter {
    return Filter.from(this.filterFrom(value)).withoutFields(fields);
  }

  toFacet(field: string, filter: unknown = {}): GroupProjection & { where: Record<string, unknown> } {
    return { ...this.axis(field).groupBy(), where: this.toWhere(this.withoutFields(filter, [field])) };
  }

  sortFrom(value: unknown = this.contract.sort.default): QuerySort[] {
    const entries: unknown[] = Array.isArray(value)
      ? value
      : isRecord(value)
        ? Object.entries(value).map(([field, direction]) => ({ field, direction }))
        : fail("sort", "expected a sort array or order record");
    const seen = new Set<string>();
    return entries.map((entry, index) => {
      const path = `sort[${index}]`;
      if (!isRecord(entry) || typeof entry.field !== "string" || !["ASC", "DESC", "asc", "desc"].includes(String(entry.direction))) {
        return fail(path, "expected field and ASC/DESC direction");
      }
      if (Object.keys(entry).some((key) => key !== "field" && key !== "direction")) return fail(path, "unknown sort property");
      if (!this.fields[entry.field]?.sort) return fail(`${path}.field`, `field "${entry.field}" cannot be sorted`);
      if (seen.has(entry.field)) return fail(path, `duplicate sort field "${entry.field}"`);
      seen.add(entry.field);
      return { field: entry.field, direction: String(entry.direction).toUpperCase() as "ASC" | "DESC" };
    });
  }

  toOrderBy(value?: unknown): Record<string, unknown> {
    return Object.fromEntries(this.sortFrom(value).map(({ field, direction }) => [this.fields[field]!.sort!.field, direction.toLowerCase()]));
  }

  toSorters(value?: unknown): { field: string; order: "asc" | "desc" }[] {
    return this.sortFrom(value).map(({ field, direction }) => ({ field: this.fields[field]!.sort!.field, order: direction === "ASC" ? "asc" : "desc" }));
  }

  /** Whole client row models fetch every executable local value once. */
  clientSelection(): string[] {
    return [...new Set(Object.values(this.fields).flatMap((field) => field.filter || field.sort ? field.row?.paths ?? [] : []))];
  }

  /** A declared local scalar accessor, also used by hidden table sort columns. */
  value(field: string, row: unknown): unknown {
    const declaration = this.fields[field];
    const projection = declaration?.row;
    if (!projection) return fail(`query.${field}`, "field has no client value projection");
    for (const path of projection.paths) {
      if (readPath(row, path) === undefined) return fail(`query.${field}`, `missing selected value path "${path}"`);
    }
    const selected = readPath(row, projection.path) ?? null;
    const mapping = declaration.filter?.valueMap?.find((entry) => Object.is(entry.from, selected));
    const value = mapping ? mapping.to : selected;
    const scalar = declaration.filter?.scalar ?? declaration.scalar;
    if (value != null && (scalar === "Date" || scalar === "DateTime")) return temporalValue(value, scalar);
    return value;
  }

  /** Decimal strings need numeric ordering without losing their wire precision. */
  comparator(field: string): ((leftRow: unknown, rightRow: unknown) => number) | undefined {
    if (this.fields[field]?.scalar !== "Decimal") return undefined;
    return (leftRow, rightRow) => {
      const left = this.value(field, leftRow), right = this.value(field, rightRow);
      if (left == null || right == null) return left === right ? 0 : left == null ? 1 : -1;
      return compareDecimal(left, right, `sort.${field}`);
    };
  }

  /** Native in-memory predicate for TanStack's row-model adapter. */
  matches(row: unknown, value: unknown = {}): boolean {
    const evaluate = (filter: QueryFilter): boolean => Object.entries(filter).every(([field, operand]) => {
      if (field === "AND") return (operand as readonly QueryFilter[]).every(evaluate);
      if (field === "OR") return (operand as readonly QueryFilter[]).some(evaluate);
      if (field === "NOT") return !evaluate(operand as QueryFilter);
      const declaration = this.fields[field]!;
      const raw = this.value(field, row);
      const scalar = declaration.filter!.scalar;
      const current = scalar === "ID" && raw != null ? String(raw) : raw;
      return Object.entries(operand as QueryFilter).every(([operator, comparison]) =>
        matchesComparison(current, operator as FilterOperator, comparison, scalar, `filter.${field}.${operator}`),
      );
    });
    return evaluate(this.filterFrom(value));
  }

  private resolveAxis(group: GroupSpec | GroupAxis): GroupAxis {
    return group instanceof GroupAxis ? this.group(group.spec) : this.group(group);
  }

  private parseFilter(value: unknown, path: string): QueryFilter {
    if (!isRecord(value)) return fail(path, "expected a filter object");
    const result: Record<string, FilterValue> = {};
    for (const [name, operand] of Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) {
      const fieldPath = `${path}.${name}`;
      if (name === "AND" || name === "OR") {
        const branches = Array.isArray(operand) ? operand : [operand];
        result[name] = branches.map((branch, index) => this.parseFilter(branch, `${fieldPath}[${index}]`));
        continue;
      }
      if (name === "NOT") { result[name] = this.parseFilter(operand, fieldPath); continue; }
      const field = this.fields[name];
      if (!field?.filter) return fail(fieldPath, "unknown or non-filterable field");
      const comparisons = operand as QueryFilter;
      if (Object.keys(comparisons).length === 0) return fail(fieldPath, "expected at least one comparison");
      const output: Record<string, FilterValue> = {};
      for (const [name, value] of Object.entries(comparisons).sort(([a], [b]) => a.localeCompare(b))) {
        if (!(FILTER_OPERATORS as readonly string[]).includes(name) || !field.filter.operators.includes(name as FilterOperator)) {
          return fail(`${fieldPath}.${name}`, "operator is not supported by this field");
        }
        const operator = name as FilterOperator;
        output[operator] = this.operand(field, operator, value, `${fieldPath}.${operator}`);
      }
      result[name] = output;
    }
    return result;
  }

  private operand(field: QueryField, operator: FilterOperator, value: unknown, path: string): FilterValue {
    if (operator === "isNull") return typeof value === "boolean" ? value : fail(path, "expected a boolean");
    if (operator === "inList" || operator === "notInList") {
      if (!Array.isArray(value)) return fail(path, "expected an array");
      return value.map((item, index) => this.scalar(field, item, `${path}[${index}]`));
    }
    if (TEXT_OPERATORS.includes(operator) || operator === "hasKey") return typeof value === "string" ? value : fail(path, "expected a string");
    if (operator === "hasKeysAny" || operator === "hasKeysAll") {
      if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) return fail(path, "expected an array of strings");
      return [...value];
    }
    if (operator === "jsonContains" || operator === "jsonContainedIn") return jsonValue(value, path);
    return this.scalar(field, value, path);
  }

  private scalar(field: QueryField, value: unknown, path: string): FilterValue {
    const scalar = field.filter?.scalar;
    if (value === null) return fail(path, "use isNull for a null comparison");
    if (scalar === "JSON") return jsonValue(value, path);
    if (scalar === "ID") {
      if (typeof value !== "string" && !(typeof value === "number" && Number.isFinite(value))) return fail(path, "expected a public identity");
      return String(value);
    }
    if (scalar === "Enum") {
      const values = field.filter?.values ?? [];
      if (typeof value !== "string" || (values.length > 0 && !values.some((item) => item.value === value))) return fail(path, "expected a declared enum value");
      return value;
    }
    if (scalar === "Boolean") return typeof value === "boolean" ? value : fail(path, "expected a boolean");
    if (scalar === "Int" || scalar === "Float") {
      if (typeof value !== "number" || !Number.isFinite(value) || (scalar === "Int" && !Number.isInteger(value))) return fail(path, `expected ${scalar === "Int" ? "an integer" : "a finite number"}`);
      if (scalar === "Int" && (value < -(2 ** 31) || value > 2 ** 31 - 1)) return fail(path, "expected a signed 32-bit integer");
      return value;
    }
    if (scalar === "Decimal") {
      if ((typeof value === "number" && Number.isFinite(value)) || (typeof value === "string" && /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value))) return String(value);
      return fail(path, "expected a decimal");
    }
    if (scalar === "Date" || scalar === "DateTime") {
      if (typeof value !== "string" || !validCalendarDate(value, scalar)) return fail(path, "expected an ISO date");
      return value;
    }
    if (scalar === "Time") {
      if (typeof value !== "string" || !validTime(value)) return fail(path, "expected an ISO time");
      return value;
    }
    if (scalar && scalar !== "Unknown") return typeof value === "string" ? value : fail(path, "expected a string");
    if (typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return value;
    return fail(path, "expected a scalar value");
  }

  private where(filter: QueryFilter): Record<string, unknown> {
    const conditions: Record<string, unknown>[] = [];
    for (const [field, operand] of Object.entries(filter)) {
      if (field === "AND" || field === "OR") {
        conditions.push({ [field === "AND" ? "_and" : "_or"]: (operand as readonly QueryFilter[]).map((branch) => this.where(branch)) });
      } else if (field === "NOT") {
        conditions.push({ _not: this.where(operand as QueryFilter) });
      } else {
        for (const [operator, value] of Object.entries(operand as QueryFilter)) {
          const clause: Record<string, unknown> = {};
          writePath(clause, this.fields[field]!.filter!.field, { [WIRE_OPERATORS[operator as FilterOperator]]: wireOperand(operator as FilterOperator, value) });
          conditions.push(clause);
        }
      }
    }
    return mergeConditions(conditions);
  }
}

/** Resolved group semantics shared by client lanes, server buckets and facets. */
export class GroupAxis {
  readonly id: string;
  readonly extraction: QueryExtraction | undefined;
  constructor(private readonly query: ResourceQuery, readonly declaration: QueryAxis, readonly spec: GroupSpec) {
    this.id = spec.granularity ? `${spec.field}:${spec.granularity}` : spec.field;
    this.extraction = spec.granularity ? declaration.extractions.find((item) => item.name === spec.granularity) : undefined;
    if (spec.granularity && !this.extraction) throw new QueryParseError(`groups.${spec.field}.granularity`, `unsupported extraction "${spec.granularity}"`);
  }
  get field(): string { return this.spec.field; }
  get granularity(): string | undefined { return this.spec.granularity; }
  get selection(): string[] {
    if (!this.declaration.identityPath) throw new QueryParseError(`groups.${this.field}`, "axis does not support client grouping");
    return [...this.declaration.paths];
  }
  identity(row: unknown): FilterPrimitive {
    const path = this.declaration.identityPath;
    if (!path) return fail(`groups.${this.field}`, "axis does not support client grouping");
    const value = readPath(row, path);
    if (value === undefined) {
      if (this.declaration.kind === "json" && this.declaration.paths.every((selection) => readPath(row, selection) !== undefined)) return null;
      return fail(`groups.${this.field}`, `missing selected identity path "${path}"`);
    }
    if (value === null) return null;
    if (this.extraction) return dateGroup(value, this.extraction.name, `groups.${this.field}`);
    if (this.declaration.kind === "date") return temporalValue(value, this.query.fields[this.field]?.scalar ?? "DateTime");
    if (typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return this.canonicalValue(value);
    if (this.declaration.kind === "json") return JSON.stringify(jsonValue(value, `groups.${this.field}`));
    return fail(`groups.${this.field}`, "group identity must be a scalar");
  }
  label(row: unknown): FilterPrimitive {
    if (!this.declaration.labelPath) return this.identity(row);
    const value = readPath(row, this.declaration.labelPath);
    if (value === undefined) return fail(`groups.${this.field}`, `missing selected label path "${this.declaration.labelPath}"`);
    if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
    return fail(`groups.${this.field}`, "group label must be a scalar");
  }
  groupBy(): GroupProjection {
    const server = this.declaration.server;
    if (!server) return fail(`groups.${this.field}`, "axis does not support server grouping");
    const identity: GroupDimension = {
      input: server.input, key: this.extraction?.key ?? server.key,
      ...(this.extraction ? { granularity: this.extraction.input } : {}),
      ...(this.extraction?.rangeKey ? { rangeKey: this.extraction.rangeKey } : {}),
    };
    const label = server.labelInput && server.labelKey ? { input: server.labelInput, key: server.labelKey } : undefined;
    // Labels are not unique: identity breaks ties so bucket pages remain stable.
    const order = label ? [label, identity] : [identity];
    return { dimensions: label ? [identity, label] : [identity], valueKey: identity.key, ...(label ? { labelKey: label.key } : {}), orderBy: order.map(({ key }) => ({ field: key, direction: "ASC", nulls: "LAST" })) };
  }
  bucketIdentity(bucket: GroupBucket): FilterPrimitive {
    const key = this.groupBy().valueKey;
    const value = bucket.key?.[key];
    if (value === undefined) return fail(`groups.${this.field}`, `bucket is missing key "${key}"`);
    if (value === null || (this.extraction?.rangeKey && value === "")) return null;
    if (this.extraction && typeof value !== "number") return dateGroup(value, this.extraction.name, `groups.${this.field}`);
    if (!this.extraction && this.declaration.kind === "date") return temporalValue(value, this.query.fields[this.field]?.scalar ?? "DateTime");
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return this.canonicalValue(value);
    if (this.declaration.kind === "json") return JSON.stringify(jsonValue(value, `groups.${this.field}`));
    return fail(`groups.${this.field}`, "bucket identity must be a scalar");
  }
  bucketLabel(bucket: GroupBucket): FilterPrimitive {
    const key = this.groupBy().labelKey;
    if (!key) return this.bucketIdentity(bucket);
    const value = bucket.key?.[key];
    if (value === undefined) return fail(`groups.${this.field}`, `bucket is missing label key "${key}"`);
    if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
    return fail(`groups.${this.field}`, "bucket label must be a scalar");
  }
  private canonicalValue(value: FilterPrimitive): FilterPrimitive {
    const field = this.query.fields[this.field];
    if (field?.kind !== "enum") return value;
    const mapped = field.filter?.valueMap?.find((entry) => Object.is(entry.to, value));
    if (!mapped) return value;
    const symbol = mapped.from;
    if (symbol === null || typeof symbol === "string" || typeof symbol === "number" || typeof symbol === "boolean") return symbol;
    return fail(`groups.${this.field}`, "enum value map must identify a scalar symbol");
  }
  drill(bucket: GroupBucket): QueryFilter | undefined {
    const drill = this.extraction ? this.extraction.drill : this.declaration.drill;
    if (!drill) return undefined;
    const value = bucket.key?.[drill.valueKey];
    if (value === undefined) return fail(`groups.${this.field}`, `bucket is missing key "${drill.valueKey}"`);
    if (value === null || (drill.kind === "range" && value === "")) {
      if (drill.nullMode === "unavailable") return undefined;
      if (drill.nullMode === "isNull") return this.query.filterFrom({ [drill.field]: { isNull: true } });
    }
    if (drill.kind === "range") {
      const range = drill.rangeKey ? bucket.key?.[drill.rangeKey] : undefined;
      if (!isRecord(range)) return fail(`groups.${this.field}`, `bucket is missing range "${drill.rangeKey ?? ""}"`);
      const scalar = this.query.fields[drill.field]?.scalar;
      return this.query.filterFrom({ [drill.field]: { gte: dateBoundary(range.from, scalar), lt: dateBoundary(range.to, scalar) } });
    }
    const mapped = drill.valueMap.find((item) => Object.is(item.from, value));
    let operand = mapped ? mapped.to : value;
    if (drill.valueTransform === "json" && typeof operand === "string") {
      try { operand = JSON.parse(operand); } catch { /* A JSON string scalar remains a string. */ }
    }
    if (drill.kind === "json" && drill.jsonPath) {
      for (const key of drill.jsonPath.split(".").reverse()) operand = { [key]: operand };
    }
    return this.query.filterFrom({ [drill.field]: { [drill.kind === "json" ? "jsonContains" : "exact"]: operand } });
  }
}

function parse<T>(schema: v.GenericSchema<unknown, T>, value: unknown, path: string): T {
  const result = v.safeParse(schema, value);
  if (result.success) return result.output;
  const issue = result.issues[0]!;
  const suffix = issue.path?.map(({ key }) => typeof key === "number" ? `[${key}]` : `.${String(key)}`).join("") ?? "";
  return fail(`${path}${suffix}`, issue.message);
}
function fail(path: string, message: string): never { throw new QueryParseError(path, message); }
function readPath(row: unknown, path: string): unknown {
  let current = row;
  for (const key of path.split(".")) {
    if (current === null) return null;
    if (!isRecord(current) || !Object.hasOwn(current, key)) return undefined;
    current = current[key];
  }
  return current;
}
function writePath(target: Record<string, unknown>, path: string, value: unknown): void {
  const segments = path.split(".");
  for (const key of segments.slice(0, -1)) {
    const child: Record<string, unknown> = {};
    target[key] = child;
    target = child;
  }
  target[segments[segments.length - 1]!] = value;
}
function mergeConditions(conditions: readonly Record<string, unknown>[]): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  for (const condition of conditions) {
    for (const [key, value] of Object.entries(condition)) {
      if (!(key in merged)) { merged[key] = value; continue; }
      if (isRecord(value) && isRecord(merged[key]) && Object.keys(value).every((child) => !(child in (merged[key] as object)))) {
        merged[key] = { ...merged[key] as Record<string, unknown>, ...value };
      } else return { _and: conditions };
    }
  }
  return merged;
}
function wireOperand(operator: FilterOperator, value: FilterValue): FilterValue {
  if (["contains", "iContains", "startsWith", "iStartsWith", "endsWith", "iEndsWith"].includes(operator)) {
    const escaped = String(value).replace(/[\\%_]/g, "\\$&");
    if (operator === "contains" || operator === "iContains") return `%${escaped}%`;
    if (operator === "startsWith" || operator === "iStartsWith") return `${escaped}%`;
    return `%${escaped}`;
  }
  return value;
}
function dateBoundary(value: unknown, scalar: string | null | undefined): string {
  if (typeof value !== "string" && typeof value !== "number" && !(value instanceof Date)) return fail("bucket.range", "expected a date boundary");
  const normalized = typeof value === "string" ? normalizeDate(value) : value;
  if (typeof normalized === "string" && !validCalendarDate(normalized, "DateTime")) return fail("bucket.range", "invalid date boundary");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return fail("bucket.range", "invalid date boundary");
  return scalar === "Date" ? date.toISOString().slice(0, 10) : date.toISOString();
}
function normalizeDate(value: string): string {
  const normalized = value.replace(/^(\d{4}-\d{2}-\d{2})\s+(\d)/, "$1T$2");
  return /^\d{4}-\d{2}-\d{2}T/.test(normalized) && !/(Z|[+-]\d{2}:?\d{2})$/.test(normalized) ? `${normalized}Z` : normalized;
}
function dateGroup(value: unknown, extraction: string, path: string): string | number {
  if (typeof value !== "string" && typeof value !== "number" && !(value instanceof Date)) return fail(path, "expected a date group value");
  const normalized = typeof value === "string" ? normalizeDate(value) : value;
  if (typeof normalized === "string" && !validCalendarDate(normalized, "DateTime")) return fail(path, "invalid date group value");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return fail(path, "invalid date group value");
  const parts: Readonly<Record<string, number>> = {
    year_number: date.getUTCFullYear(), quarter_number: Math.floor(date.getUTCMonth() / 3) + 1,
    month_number: date.getUTCMonth() + 1, day_of_year: Math.floor((date.getTime() - Date.UTC(date.getUTCFullYear(), 0, 1)) / 86400000) + 1,
    day_of_month: date.getUTCDate(), day_of_week: date.getUTCDay(), hour_number: date.getUTCHours(), minute_number: date.getUTCMinutes(), second_number: date.getUTCSeconds(),
  };
  if (Object.hasOwn(parts, extraction)) return parts[extraction]!;
  if (extraction === "iso_week_number") {
    const thursday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    thursday.setUTCDate(thursday.getUTCDate() + 3 - ((thursday.getUTCDay() + 6) % 7));
    return 1 + Math.floor((thursday.getTime() - Date.UTC(thursday.getUTCFullYear(), 0, 1)) / (7 * 86400000));
  }
  const year = String(date.getUTCFullYear());
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  if (extraction === "year") return year;
  if (extraction === "quarter") return `${year}-Q${Math.floor(date.getUTCMonth() / 3) + 1}`;
  if (extraction === "month") return `${year}-${month}`;
  if (extraction === "week") { date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7)); return date.toISOString().slice(0, 10); }
  if (extraction === "day") return date.toISOString().slice(0, 10);
  if (extraction === "hour") return date.toISOString().slice(0, 13);
  if (extraction === "minute") return date.toISOString().slice(0, 16);
  if (extraction === "second") return date.toISOString().slice(0, 19);
  return fail(path, `client extraction "${extraction}" is unsupported`);
}

function matchesComparison(value: unknown, operator: FilterOperator, operand: FilterValue, scalar: string, path: string): boolean {
  const equal = (right: unknown): boolean => {
    if (value == null) return value === right;
    if (scalar === "JSON") return JSON.stringify(jsonValue(value, path)) === JSON.stringify(jsonValue(right, path));
    if (scalar === "Date" || scalar === "DateTime") return temporalValue(value, scalar) === temporalValue(right, scalar);
    if (scalar === "Decimal") return compareDecimal(value, right, path) === 0;
    return value === right;
  };
  if (operator === "isNull") return (value == null) === operand;
  if (operator === "exact") return equal(operand);
  if (operator === "ne") return !equal(operand);
  if (operator === "inList") return (operand as readonly FilterValue[]).some(equal);
  if (operator === "notInList") return !(operand as readonly FilterValue[]).some(equal);
  if (operator === "jsonContains") return jsonContains(value, operand);
  if (operator === "jsonContainedIn") return jsonContains(operand, value);
  if (operator === "hasKey") return (isRecord(value) && Object.hasOwn(value, String(operand))) || (Array.isArray(value) && value.includes(operand));
  if (operator === "hasKeysAny" || operator === "hasKeysAll") {
    const present = (key: FilterValue): boolean => (isRecord(value) && Object.hasOwn(value, String(key))) || (Array.isArray(value) && value.includes(key));
    return operator === "hasKeysAny" ? (operand as readonly FilterValue[]).some(present) : (operand as readonly FilterValue[]).every(present);
  }
  if (ORDER_OPERATORS.includes(operator)) {
    if (value == null) return false;
    const compared = compareValues(value, operand, scalar, path);
    if (operator === "gt") return compared > 0;
    if (operator === "gte") return compared >= 0;
    if (operator === "lt") return compared < 0;
    return compared <= 0;
  }
  if (operator === "similar" || operator === "notSimilar") return fail(path, "SQL SIMILAR is not executable by the client row model");
  if (["like", "iLike", "notLike", "notILike"].includes(operator)) {
    const insensitive = operator === "iLike" || operator === "notILike";
    const pattern = String(operand);
    const match = value != null && (/[\\%_]/.test(pattern)
      ? new RegExp(likeRegex(pattern), insensitive ? "is" : "s").test(String(value))
      : (insensitive ? String(value).toLowerCase().includes(pattern.toLowerCase()) : String(value).includes(pattern)));
    return operator === "notLike" || operator === "notILike" ? !match : match;
  }
  if (["regex", "iRegex", "notRegex", "notIRegex"].includes(operator)) {
    let regex: RegExp;
    try { regex = new RegExp(String(operand), operator === "iRegex" || operator === "notIRegex" ? "i" : ""); }
    catch { return fail(path, "invalid regular expression"); }
    const match = value != null && regex.test(String(value));
    return operator === "notRegex" || operator === "notIRegex" ? !match : match;
  }
  if (value == null) return false;
  const insensitive = operator.startsWith("i");
  const text = insensitive ? String(value).toLowerCase() : String(value);
  const needle = insensitive ? String(operand).toLowerCase() : String(operand);
  if (operator === "contains" || operator === "iContains") return text.includes(needle);
  if (operator === "startsWith" || operator === "iStartsWith") return text.startsWith(needle);
  if (operator === "endsWith" || operator === "iEndsWith") return text.endsWith(needle);
  return fail(path, "operator cannot be evaluated by the client row model");
}
function likeRegex(pattern: string): string {
  let regex = "^";
  let escaped = false;
  const literal = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (const character of pattern) {
    if (escaped) { regex += literal(character); escaped = false; }
    else if (character === "\\") escaped = true;
    else if (character === "%") regex += ".*";
    else if (character === "_") regex += ".";
    else regex += literal(character);
  }
  if (escaped) regex += "\\\\";
  return `${regex}$`;
}
function jsonContains(value: unknown, operand: unknown, nested = false): boolean {
  if (isRecord(operand)) return isRecord(value) && Object.entries(operand).every(([key, item]) => Object.hasOwn(value, key) && jsonContains(value[key], item, true));
  if (Array.isArray(operand)) return Array.isArray(value) && operand.every((wanted) => value.some((item) => jsonContains(item, wanted, true)));
  if (Array.isArray(value) && !nested) return value.some((item) => JSON.stringify(item) === JSON.stringify(operand));
  return value === operand;
}

/** Canonical ISO clock spelling, including native fractional and timezone offsets. */
function validTime(value: string): boolean {
  const match = /^(\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?(?:Z|[+-](\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d+)?)?)?$/.exec(value);
  if (!match || match[0] !== value) return false;
  const hour = Number(match[1]), minute = Number(match[2]), second = Number(match[3] ?? 0);
  // Python's native time parser accepts 24:00 as midnight, at microsecond precision.
  const midnight = hour === 24 && minute === 0 && second === 0 && Number((match[4] ?? "").slice(0, 6)) === 0;
  return (hour < 24 || midnight) && minute < 60 && second < 60
    && Number(match[5] ?? 0) < 24 && Number(match[6] ?? 0) < 60 && Number(match[7] ?? 0) < 60;
}

function validCalendarDate(value: string, scalar: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})(.*)$/.exec(value);
  if (!match || match[0] !== value || (scalar === "Date" ? match[4] !== "" : match[4] !== "" && !match[4]!.startsWith("T"))) return false;
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]! && !Number.isNaN(new Date(normalizeDate(value)).getTime());
}
function compareValues(left: unknown, right: unknown, scalar: string, path: string): number {
  if (scalar === "Decimal") return compareDecimal(left, right, path);
  const comparable = (value: unknown): string | number => scalar === "Date" || scalar === "DateTime" ? temporalValue(value, scalar) : typeof value === "number" ? value : String(value);
  const a = comparable(left), b = comparable(right);
  return a < b ? -1 : a > b ? 1 : 0;
}
function compareDecimal(left: unknown, right: unknown, path: string): number {
  const parts = (value: unknown) => {
    if (typeof value !== "string" && typeof value !== "number") return fail(path, "expected a decimal");
    const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i.exec(String(value));
    if (!match) return fail(path, "expected a decimal");
    const fraction = match[3] ?? match[4] ?? "";
    const digits = `${match[2] ?? ""}${fraction}`.replace(/^0+/, "") || "0";
    return { digits, order: BigInt(digits.length) + BigInt(match[5] ?? "0") - BigInt(fraction.length), sign: digits === "0" ? 0 : match[1] === "-" ? -1 : 1 };
  };
  const a = parts(left), b = parts(right);
  if (a.sign !== b.sign) return a.sign < b.sign ? -1 : 1;
  if (a.sign === 0) return 0;
  if (a.order !== b.order) return (a.order < b.order ? -1 : 1) * a.sign;
  const length = Math.max(a.digits.length, b.digits.length);
  const aDigits = a.digits.padEnd(length, "0"), bDigits = b.digits.padEnd(length, "0");
  return (aDigits < bDigits ? -1 : aDigits > bDigits ? 1 : 0) * a.sign;
}

function temporalValue(value: unknown, scalar: string): string {
  if (typeof value !== "string" && typeof value !== "number" && !(value instanceof Date)) return fail("date", "expected a date value");
  const normalized = typeof value === "string" ? normalizeDate(value) : value;
  if (typeof normalized === "string" && !validCalendarDate(normalized, scalar)) return fail("date", "invalid date value");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return fail("date", "invalid date value");
  const iso = date.toISOString();
  if (scalar === "Date") return iso.slice(0, 10);
  // Keep sub-millisecond source precision instead of merging distinct SQL timestamps.
  const fraction = typeof normalized === "string" ? /T\d{2}:\d{2}:\d{2}\.(\d+)/.exec(normalized)?.[1] ?? "" : iso.slice(20, 23);
  return `${iso.slice(0, 19)}.${fraction.slice(0, 6).padEnd(9, "0")}Z`;
}
