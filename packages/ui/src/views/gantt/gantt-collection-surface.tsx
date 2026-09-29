import * as React from "react";
import { Filter, ResourceQuery, rowPublicId, type Row } from "@angee/metadata";
import { MAX_PAGE_SIZE, useAngeeListBatch } from "@angee/refine";
import { getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useNavigate } from "@tanstack/react-router";
import { addDays } from "date-fns";
import { dateFromUnknown } from "../../widgets/date-format";
import { errorFromUnknown } from "../../data/errors";
import { useUiT } from "../../i18n";
import { toneColorVar } from "../../lib/tones";
import { useValueStable } from "../../lib/use-value-stable";
import { useStatusTone } from "../../widgets/use-status-tone";
import { useRelationOptions } from "../relation/relation-options";
import { ResourceListFrame } from "../resource/ResourceListFrame";
import { listBatchTarget } from "../resource/resource-operations";
import { useResourceToolbarProps } from "../resource/resource-toolbar-props";
import { useListViewToolbarInputs, type ListViewToolbarInputsProps } from "../resource/resource-view-toolbar-inputs";
import { readPath } from "../resource/resource-view-list-body";
import { useResourceListQuery } from "../resource/surface/resource-list-query";
import { listResultFromPageState, useResourceRowsSnapshot, useResourceViewQueryFacts, useResourceViewTableState } from "../resource/surface/table-state";
import type { UseResourceViewSurfaceProps } from "../resource/resource-view-surface";
import { rowGroupsFromLaneSource, type ResolvedBoardLaneSource } from "../resource/resource-view-board-lanes";
import type { BoardCardPlacement, GanttViewSpec, ListViewProps } from "../resource/resource-view-types";
import { calendarAnchorToDate, calendarDateToAnchor } from "../calendar/calendar-view-controls";
import { GanttView, type GanttEvent, type GanttResource } from "./GanttView";

const NO_PLACEMENTS: ReadonlyMap<string, BoardCardPlacement> = new Map();

interface GanttCollectionSurfaceProps<TRow extends Row> extends Pick<ListViewProps<TRow>,
  "availableViews" | "onCreate" | "createLabel" | "toolbarActions" | "className" | "presentation" | "toolbarWrap" | "onRowClick" | "rowHref" | "maxGroupDepth"> {
  surfaceProps: UseResourceViewSurfaceProps<TRow>;
  gantt: GanttViewSpec;
  laneSource: ResolvedBoardLaneSource;
  groupingPinned: boolean;
  toolbarInputs: Omit<ListViewToolbarInputsProps<TRow>, "rows" | "list">;
}

/** Pages the related row catalogue; native list queries load all bars on that page. */
export function GanttCollectionSurface<TRow extends Row>({
  surfaceProps, gantt, laneSource, groupingPinned, toolbarInputs: input, availableViews,
  onCreate, createLabel, toolbarActions, className, presentation, toolbarWrap, maxGroupDepth, onRowClick, rowHref,
}: GanttCollectionSurfaceProps<TRow>) {
  const t = useUiT();
  const navigate = useNavigate();
  const resolveTone = useStatusTone();
  const { resourceView, modelMetadata, columns, onListStateChange } = surfaceProps;
  const groupStack = surfaceProps.groupStack ?? resourceView.state.groupStack;
  const { start: startField, end: endField, label: labelField, tone: toneField, rowFields, renderRowContent } = gantt;
  const metadata = modelMetadata ?? null;
  const dataResource = metadata?.resource ?? null;
  const query = React.useMemo(() => metadata ? ResourceQuery.from(metadata) : null, [metadata]);
  const pageSize = Math.min(MAX_PAGE_SIZE, resourceView.state.pagination.pageSize);
  const catalogue = useRelationOptions(laneSource.relation, {
    labelField: laneSource.labelField,
    fields: rowFields,
    page: resourceView.state.pagination.pageIndex + 1,
    pageSize,
    filters: laneSource.filters,
    sorters: laneSource.sorters ?? [{ field: "id", order: "asc" }],
  });
  const lanes = catalogue.options;
  const label = labelField ?? dataResource?.recordRepresentation ?? "id";
  const fields = useValueStable([...(surfaceProps.fields ?? []), startField, endField, label, ...(toneField ? [toneField] : [])]);
  const facts = useResourceViewQueryFacts({
    columns, fields, filter: surfaceProps.filter, order: surfaceProps.order,
    resourceView, modelMetadata: metadata, laneSource, groupStack,
  });
  // Offset pages need a stable tie-breaker while retaining the view's order.
  const barOrder = useValueStable({
    ...facts.sortOrder,
    ...(query?.fields[query.contract.identity.field]?.sort
      ? { [query.contract.identity.field]: facts.sortOrder?.[query.contract.identity.field] ?? "ASC" }
      : {}),
  });
  const barScope = React.useMemo(() => {
    try {
      if (!query || !lanes.length) return { filter: undefined, error: null };
      const axis = query.group({ field: laneSource.field });
      const drill = axis.declaration.drill;
      if (!drill) throw new Error(`Gantt row field "${laneSource.field}" requires a drill filter.`);
      const filters = lanes.map((lane) => {
        const filter = axis.drill({ key: { [drill.valueKey]: lane.value } });
        if (!filter) throw new Error(`Gantt row field "${laneSource.field}" cannot filter its related rows.`);
        return filter;
      });
      return { filter: Filter.combineOptional(facts.mergedFilter, { OR: filters }), error: null };
    } catch (cause) {
      return { filter: undefined, error: errorFromUnknown(cause) };
    }
  }, [query, lanes, laneSource.field, facts.mergedFilter]);
  const enabled = Boolean(query && lanes.length && !barScope.error);
  const first = useResourceListQuery({
    resource: dataResource,
    scope: { filter: barScope.filter, order: barOrder, page: 1, pageSize: MAX_PAGE_SIZE },
    fields: facts.requestedFields,
    enabled,
  });
  const requests = React.useMemo(() => {
    if (!enabled || !query || first.result.total === undefined) return [];
    return Array.from({ length: Math.max(0, Math.ceil(first.result.total / MAX_PAGE_SIZE) - 1) }, (_, index) => ({
      key: String(index + 2), page: index + 2, pageSize: MAX_PAGE_SIZE,
      where: query.toWhere(barScope.filter), orderBy: query.toOrderBy(barOrder),
    }));
  }, [enabled, query, first.result.total, barScope.filter, barOrder]);
  const rest = useAngeeListBatch(listBatchTarget(dataResource), requests, { fields: facts.requestedFields, enabled });
  const records = React.useMemo(() => {
    const loaded = enabled
      ? [...first.result.data, ...[...rest.values()].flatMap((page) => page.rows)] as TRow[]
      : [];
    const rows = loaded.filter((row) => Boolean(rowPublicId(row)));
    return { rows, skipped: loaded.length - rows.length };
  }, [enabled, first.result.data, rest]);
  const { rows } = records;
  const pages = [...rest.values()];
  const fetching = catalogue.list.fetching || (enabled && (first.query.isFetching || pages.some((page) => page.fetching)));
  const catalogueError = React.useMemo(() => catalogue.list.error ? new Error(catalogue.list.error) : null, [catalogue.list.error]);
  const error = barScope.error ?? catalogueError ?? (enabled ? first.query.error ?? pages.find((page) => page.error)?.error : null);
  const refetch = React.useCallback(() => {
    catalogue.list.refetch();
    if (enabled) void first.query.refetch();
    for (const page of rest.values()) page.refetch();
  }, [catalogue.list.refetch, enabled, first.query.refetch, rest]);
  const list = listResultFromPageState({ resourceView, rows, total: catalogue.list.total, pageSize, fetching, error, refetch });
  // Record navigation stays within the loaded row page. Catalogue pagination
  // cannot be serialized as a record-list page scope.
  useResourceRowsSnapshot<TRow>({ ...list, total: rows.length, page: 1,
    pageSize: Math.max(1, rows.length), pageCount: 1, hasNext: false, hasPrev: false,
  }, { onListStateChange });
  const tableState = useResourceViewTableState({
    columns, resourceView, modelMetadata: metadata, groupStack, query: query ?? undefined, sortOrder: facts.sortOrder, maxPageSize: MAX_PAGE_SIZE,
  });
  const tableColumns = React.useMemo(() => [...tableState.tableColumns], [tableState.tableColumns]);
  const table = useReactTable({
    data: rows, columns: tableColumns,
    getRowId: (row, index) => rowPublicId(row) ?? String(index),
    getCoreRowModel: getCoreRowModel(), manualPagination: true,
  });
  const tableRows = table.getCoreRowModel().rows;
  const projection = React.useMemo(() => {
    try {
      const groups = rowGroupsFromLaneSource(tableRows, laneSource, lanes, NO_PLACEMENTS, t("list.emptyValue"), t("list.unknownValue"));
      const resources: GanttResource[] = groups.map((group) => ({ id: group.key, title: group.label ?? t("list.emptyValue") }));
      let skipped = records.skipped;
      const events: GanttEvent[] = groups.flatMap((group) => group.rows.flatMap(({ id, original: row }) => {
        const startValue = readPath(row, startField);
        const endValue = readPath(row, endField);
        // Unscheduled records have no bar; their catalogue row remains visible.
        if (startValue == null || endValue == null) return [];
        const start = dateFromUnknown(startValue);
        const end = dateFromUnknown(endValue);
        if (!start || !end || end < start) {
          skipped += 1;
          return [];
        }
        const tone = toneField ? resolveTone(String(readPath(row, toneField) ?? "")) : "brand";
        // Date fields include the target day; ReUI uses exclusive ends.
        const allDay = metadata?.fields[startField]?.scalar === "Date" && metadata?.fields[endField]?.scalar === "Date";
        return [{ id, title: String(readPath(row, label) ?? id), start, end: allDay ? addDays(end, 1) : end, allDay, resourceId: group.key,
          color: toneColorVar(tone), readOnly: true }];
      }));
      return { resources, events, skipped, error: null };
    } catch (cause) {
      return { resources: [], events: [], skipped: records.skipped, error: errorFromUnknown(cause) };
    }
  }, [metadata, tableRows, laneSource, lanes, records.skipped, startField, endField, toneField, label, resolveTone, t]);
  const toolbarInputs = useListViewToolbarInputs({ ...input, rows, list, serverGrouping: false });
  const toolbar = useResourceToolbarProps({
    ...toolbarInputs, resourceView, availableViews, view: "gantt", groupStack,
    groupingEnabled: !groupingPinned && toolbarInputs.groupingEnabled,
    favorites: resourceView.savedFavorites,
    createLabel, onCreate, actions: toolbarActions, wrap: toolbarWrap, maxGroupDepth,
    pagerMaxPageSize: MAX_PAGE_SIZE,
  });
  const anchor = React.useMemo(() => calendarAnchorToDate(resourceView.state.anchor), [resourceView.state.anchor]);
  const onDateChange = React.useCallback((date: Date) => resourceView.setAnchor(calendarDateToAnchor(date)), [resourceView.setAnchor]);
  const renderResourceContent = React.useCallback((resource: GanttResource) => {
    const row = catalogue.rows.find((candidate) => rowPublicId(candidate) === resource.id);
    return row ? renderRowContent?.(row) : null;
  }, [catalogue.rows, renderRowContent]);
  const handleEventClick = React.useCallback((event: GanttEvent) => {
    const row = rows.find((candidate) => rowPublicId(candidate) === event.id);
    if (!row) return;
    if (onRowClick) onRowClick(row);
    else if (rowHref) void navigate({ to: rowHref(row) });
  }, [rows, onRowClick, rowHref, navigate]);
  return (
    <ResourceListFrame toolbar={toolbar} presentation={presentation} className={className} error={list.error ?? projection.error} onRetry={refetch} loadingFooter={fetching}
      summary={projection.skipped ? t("gantt.skipped", { count: projection.skipped }) : undefined}>
      <GanttView
        resources={projection.resources} events={projection.events} date={anchor} onDateChange={onDateChange} defaultScale="quarter" loading={fetching}
        fitToEvents={resourceView.state.anchor === calendarDateToAnchor(new Date())} sidebarWidth={gantt.sidebarWidth} minRowHeight={gantt.minRowHeight}
        renderRowContent={renderRowContent ? renderResourceContent : undefined}
        onEventClick={onRowClick || rowHref ? handleEventClick : undefined}
      />
    </ResourceListFrame>
  );
}
