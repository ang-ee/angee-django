import * as React from "react";
import { ResourceQuery, useModelMetadata } from "@angee/metadata";
import { useAngeeAggregate, useAngeeGroupBy, type AggregateBucket, type AggregateMeasure } from "@angee/refine";
import { useAggregateOperation, useGroupOperation } from "../views/resource/resource-operations";
import { Filter, type ResourceViewFilter } from "../views/resource/resource-view-model";
import { useResourceListQuery } from "../views/resource/surface/resource-list-query";
import type { DashboardWidgetData, WidgetSpec, WidgetSource } from "./headless";

export interface DashboardPageScope {
  resource: string;
  filter?: ResourceViewFilter;
}

const EMPTY_SOURCE: WidgetSource = { resource: "__angee_disabled__" };
const COUNT_MEASURE: AggregateMeasure = { op: "count", field: null };

function bucketValue(bucket: AggregateBucket | null, measure: AggregateMeasure): number | null {
  if (!bucket) return null;
  if (measure.op === "count" || !measure.field) return bucket.count;
  const value = bucket[measure.op]?.[measure.field];
  if (value == null) return null;
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function errorValue(error: unknown): Error | null {
  if (!error) return null;
  return error instanceof Error ? error : new Error(String(error));
}

function useVisible(): boolean {
  const [visible, setVisible] = React.useState(() =>
    typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  React.useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

export function useDashboardWidgetData(
  spec: WidgetSpec,
  pageScope?: DashboardPageScope,
): DashboardWidgetData {
  const source = spec.data.shape === "none" ? EMPTY_SOURCE : spec.data.source;
  const metadata = useModelMetadata(source.resource);
  const resource = metadata?.resource ?? null;
  const visible = useVisible();
  const prepared = React.useMemo(() => {
    if (!resource || spec.data.shape === "none") {
      return {
        error: null,
        query: null,
        where: undefined,
        groups: [],
        sort: undefined,
        fields: [] as string[],
        measure: COUNT_MEASURE,
        filter: undefined,
      };
    }
    try {
      const query = ResourceQuery.from(resource);
      const pageFilter = pageScope?.resource === source.resource ? pageScope.filter : undefined;
      const filter = Filter.combineOptional(source.filter as ResourceViewFilter | undefined, pageFilter);
      const where = query.toWhere(filter);
      const groups = query.groupsFrom(source.groups ?? []);
      const sort = query.sortFrom(source.sort);
      const logicalFields = source.fields ?? [resource.query.identity.field];
      const fields = [...new Set(logicalFields.flatMap((field) => {
        const projection = query.fields[field]?.row;
        if (!projection) throw new Error(`Field "${field}" has no readable row projection.`);
        return projection.paths;
      }))];
      const declaredMeasure = source.measure ?? COUNT_MEASURE;
      const measure = declaredMeasure.op === "count"
        ? COUNT_MEASURE
        : resource.aggregateMeasures?.find(
          (candidate) => candidate.op === declaredMeasure.op && candidate.field === declaredMeasure.field,
        );
      if (!measure) throw new Error(`Measure ${declaredMeasure.op}:${declaredMeasure.field ?? ""} is unavailable.`);
      if (spec.data.shape === "series" && groups.length !== 1) {
        throw new Error("Series widgets require exactly one group axis.");
      }
      return {
        error: null,
        query,
        where,
        groups,
        sort,
        fields,
        measure: { op: measure.op as AggregateMeasure["op"], field: measure.field, input: measure.input },
        filter,
      };
    } catch (error) {
      return {
        error: errorValue(error),
        query: null,
        where: undefined,
        groups: [],
        sort: undefined,
        fields: [] as string[],
        measure: COUNT_MEASURE,
        filter: undefined,
      };
    }
  }, [pageScope?.filter, pageScope?.resource, resource, source, spec.data.shape]);

  const refresh = spec.data.shape === "none" ? { mode: "manual" as const } : source.refresh ?? { mode: "live" as const };
  const enabled = visible && prepared.query !== null;
  const aggregateOperation = useAggregateOperation(resource);
  const groupOperation = useGroupOperation(resource);
  const wantsValue = spec.data.shape === "value" && enabled;
  const wantsSeries = spec.data.shape === "series" && enabled;
  const wantsRows = spec.data.shape === "rows" && enabled;
  const aggregate = useAngeeAggregate(wantsValue ? aggregateOperation.target : null, {
    document: aggregateOperation.document,
    measures: prepared.measure ? [prepared.measure] : [],
    where: prepared.where,
    enabled: wantsValue,
  });
  const projection = prepared.groups?.[0]?.groupBy();
  const grouped = useAngeeGroupBy(wantsSeries ? groupOperation.target : null, {
    document: groupOperation.document,
    dimensions: projection?.dimensions ?? [],
    measures: prepared.measure ? [prepared.measure] : [],
    where: prepared.where,
    pageSize: 500,
    enabled: wantsSeries,
  });
  const order = prepared.sort
    ? Object.fromEntries(prepared.sort.map((item) => [item.field, item.direction]))
    : undefined;
  const rows = useResourceListQuery({
    resource,
    fields: prepared.fields ?? ["id"],
    scope: wantsRows ? {
      filter: prepared.filter,
      order,
      page: 1,
      pageSize: Math.min(source.limit ?? 10, 100),
    } : null,
    enabled: wantsRows,
  });

  const seriesResult = React.useMemo(() => {
    const axis = prepared.groups?.[0];
    const measure = prepared.measure;
    if (!wantsSeries || !axis || !measure || grouped.totalCount > 500) {
      return { series: [], error: null };
    }
    try {
      const ranked = grouped.buckets
        .map((bucket) => ({
          key: String(axis.bucketIdentity(bucket)),
          label: String(axis.bucketLabel(bucket) ?? "—"),
          value: bucketValue(bucket, measure) ?? 0,
        }))
        .sort((left, right) => right.value - left.value || left.key.localeCompare(right.key));
      const limit = Math.min(source.limit ?? 8, 20);
      const visible = ranked.slice(0, limit);
      if (spec.kind === "donut" && ranked.length > limit) {
        const other = ranked.slice(limit).reduce((sum, point) => sum + point.value, 0);
        if (other !== 0) visible.push({ key: "__other__", label: "Other", value: other });
      }
      return { series: visible, error: null };
    } catch (cause) {
      return { series: [], error: errorValue(cause) };
    }
  }, [grouped.buckets, grouped.totalCount, prepared.groups, prepared.measure, source.limit, spec.kind, wantsSeries]);

  React.useEffect(() => {
    if (refresh.mode !== "interval" || !visible) return;
    const timer = window.setInterval(() => {
      if (wantsValue) aggregate.refetch();
      if (wantsSeries) grouped.refetch();
      if (wantsRows) void rows.query.refetch();
    }, refresh.seconds * 1_000);
    return () => window.clearInterval(timer);
  }, [aggregate.refetch, grouped.refetch, refresh, rows.query.refetch, visible, wantsRows, wantsSeries, wantsValue]);

  const updatedAt = Math.max(
    wantsValue ? aggregate.updatedAt ?? 0 : 0,
    wantsSeries ? grouped.updatedAt ?? 0 : 0,
    wantsRows ? rows.query.dataUpdatedAt : 0,
  ) || null;
  return {
    value: wantsValue ? bucketValue(aggregate.aggregate, prepared.measure ?? COUNT_MEASURE) : null,
    series: seriesResult.series,
    rows: wantsRows ? (rows.result.data as readonly Record<string, unknown>[] ?? []) : [],
    fetching: Boolean(
      (wantsValue && aggregate.fetching)
      || (wantsSeries && grouped.fetching)
      || (wantsRows && rows.query.isFetching),
    ),
    error: prepared.error
      ?? seriesResult.error
      ?? (wantsSeries && grouped.totalCount > 500 ? new Error("This series exceeds the 500-bucket dashboard limit.") : null)
      ?? errorValue(aggregate.error ?? grouped.error ?? rows.query.error),
    live: refresh.mode === "live" && visible && Boolean(resource?.roots.changes),
    updatedAt,
    refetch: () => {
      if (wantsValue) aggregate.refetch();
      if (wantsSeries) grouped.refetch();
      if (wantsRows) void rows.query.refetch();
    },
  };
}
