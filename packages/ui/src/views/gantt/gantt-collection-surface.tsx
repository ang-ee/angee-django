import * as React from "react";
import { ResourceQuery, rowPublicId, useModelMetadata, type Row } from "@angee/metadata";
import { MAX_PAGE_SIZE } from "@angee/refine";
import { getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useNavigate } from "@tanstack/react-router";
import { dateFromUnknown } from "../../widgets/date-format";
import { errorFromUnknown } from "../../data/errors";
import { useUiT } from "../../i18n";
import { toneColorVar } from "../../lib/tones";
import { useValueStable } from "../../lib/use-value-stable";
import { useStatusTone } from "../../widgets/use-status-tone";
import { useRelationOptions } from "../relation/relation-options";
import { ResourceListFrame } from "../resource/ResourceListFrame";
import { useResourceToolbarProps } from "../resource/resource-toolbar-props";
import { useListViewToolbarInputs, type ListViewToolbarInputsProps } from "../resource/resource-view-toolbar-inputs";
import { readPath } from "../resource/resource-view-list-body";
import { listResultFromPageState, useResourceRowsSnapshot, useResourceViewQueryFacts, useResourceViewTableState } from "../resource/surface/table-state";
import type { UseResourceViewSurfaceProps } from "../resource/resource-view-surface";
import { rowGroupsFromLaneSource, type ResolvedBoardLaneSource } from "../resource/resource-view-board-lanes";
import type { BoardCardPlacement, GanttViewSpec, ListViewProps } from "../resource/resource-view-types";
import { calendarAnchorToDate, calendarDateToAnchor } from "../calendar/calendar-view-controls";
import { GanttView, type GanttEvent, type GanttResource } from "./GanttView";
import { ganttLaneFilter, useGanttRecords } from "./gantt-collection-query";
import { relationValueId } from "../../widgets/types";
import { ganttBarEvent } from "./gantt-bar-event";
import { GanttLane, withGanttLaneNotes, type GanttLaneDetails } from "./gantt-lane";

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
  const { start: startField, end: endField, label: labelField, tone: toneField, lane } = gantt;
  const metadata = modelMetadata ?? null;
  const dataResource = metadata?.resource ?? null;
  const query = React.useMemo(() => metadata ? ResourceQuery.from(metadata) : null, [metadata]);
  const pageSize = Math.min(MAX_PAGE_SIZE, resourceView.state.pagination.pageSize);
  const laneMetadata = useModelMetadata(laneSource.relation.resource);
  const laneFields = React.useMemo(() => {
    if (!gantt.current) return lane?.fields;
    const field = laneMetadata?.resource.query.fields[gantt.current];
    if (!field?.row) throw new Error(`Gantt current field "${gantt.current}" has no lane value projection.`);
    return [...(lane?.fields ?? []), ...field.row.paths];
  }, [gantt.current, laneMetadata, lane?.fields]);
  const catalogue = useRelationOptions(laneSource.relation, {
    labelField: laneSource.labelField,
    fields: laneFields,
    page: resourceView.state.pagination.pageIndex + 1,
    pageSize,
    filters: laneSource.filters,
    sorters: laneSource.sorters,
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
      return { filter: ganttLaneFilter(query, laneSource.field, lanes.map((lane) => lane.value), facts.mergedFilter), error: null };
    } catch (cause) {
      return { filter: undefined, error: errorFromUnknown(cause) };
    }
  }, [query, lanes, laneSource.field, facts.mergedFilter]);
  const enabled = Boolean(query && lanes.length && !barScope.error);
  const records = useGanttRecords<TRow>({
    resource: dataResource, fields: facts.requestedFields, filter: barScope.filter, order: barOrder, enabled,
  });
  const { rows } = records;
  const markerSpec = gantt.markers;
  const markerMetadata = useModelMetadata(markerSpec?.resource ?? "");
  const markerSource = React.useMemo(() => {
    try {
      if (!markerSpec) return { fields: [], filter: undefined, order: undefined, error: null };
      if (!markerMetadata) throw new Error(`Unknown Gantt marker resource "${markerSpec.resource}".`);
      const markerQuery = ResourceQuery.from(markerMetadata);
      if (markerQuery.relation(markerSpec.lane)?.model !== laneSource.relation.resource) {
        throw new Error(`Gantt marker field "${markerSpec.lane}" must reference the lane resource "${laneSource.relation.resource}".`);
      }
      const label = markerSpec.label ?? markerMetadata.resource.recordRepresentation ?? "id";
      return {
        fields: ["id", markerSpec.date, label, ...(markerSpec.tone ? [markerSpec.tone] : []), ...markerQuery.selection([{ field: markerSpec.lane }])],
        filter: ganttLaneFilter(markerQuery, markerSpec.lane, lanes.map((lane) => lane.value), markerSpec.filter),
        order: markerQuery.fields.id?.sort ? { id: "ASC" as const } : undefined,
        error: null,
      };
    } catch (cause) {
      return { fields: [], filter: undefined, order: undefined, error: errorFromUnknown(cause) };
    }
  }, [markerSpec, markerMetadata, laneSource.relation.resource, lanes]);
  const markerFields = useValueStable(markerSource.fields);
  const markers = useGanttRecords({
    resource: markerMetadata?.resource ?? null, fields: markerFields, filter: markerSource.filter, order: markerSource.order,
    enabled: Boolean(markerSpec && lanes.length && !barScope.error && !markerSource.error),
  });
  const fetching = catalogue.list.fetching || records.fetching || markers.fetching;
  const catalogueError = React.useMemo(() => catalogue.list.error ? new Error(catalogue.list.error) : null, [catalogue.list.error]);
  const error = barScope.error ?? catalogueError ?? markerSource.error ?? records.error ?? markers.error;
  const refetch = React.useCallback(() => {
    catalogue.list.refetch();
    records.refetch();
    markers.refetch();
  }, [catalogue.list.refetch, records.refetch, markers.refetch]);
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
      const detailsByLane = new Map<string, GanttLaneDetails>(catalogue.rows.flatMap((row) => {
        const id = rowPublicId(row);
        return id && lane ? [[id, lane.content(row)] as const] : [];
      }));
      const resources: GanttResource[] = groups.map((group) => ({ id: group.key,
        title: detailsByLane.get(group.key)?.title || group.label || t("list.emptyValue") }));
      const currentByLane = new Map(catalogue.rows.map((row) => [rowPublicId(row), gantt.current
        ? relationValueId(readPath(row, gantt.current)) : ""]));
      let skipped = records.skipped + markers.skipped;
      let events: GanttEvent[] = groups.flatMap((group) => group.rows.flatMap(({ id, original: row }) => {
        const tone = toneField ? resolveTone(String(readPath(row, toneField) ?? "")) : "brand";
        const allDay = metadata?.fields[startField]?.scalar === "Date" && metadata?.fields[endField]?.scalar === "Date";
        const event = ganttBarEvent(row, { id, resourceId: group.key, start: startField, end: endField,
          label, tone, dateOnly: allDay, current: currentByLane.get(group.key) === id });
        if (!event && readPath(row, startField) != null && readPath(row, endField) != null) skipped += 1;
        return event ? [event] : [];
      }));
      events = withGanttLaneNotes(events, detailsByLane);
      if (markerSpec && markerMetadata) {
        const label = markerSpec.label ?? markerMetadata.resource.recordRepresentation ?? "id";
        const laneIds = new Set(lanes.map((lane) => lane.value));
        for (const row of markers.rows) {
          const value = readPath(row, markerSpec.date);
          if (value == null) continue;
          const date = dateFromUnknown(value);
          const resourceId = relationValueId(readPath(row, markerSpec.lane));
          if (!date || !laneIds.has(resourceId)) { skipped += 1; continue; }
          const tone = markerSpec.tone ? resolveTone(String(readPath(row, markerSpec.tone) ?? "")) : "neutral";
          events.push({
            id: JSON.stringify(["marker", markerSpec.resource, rowPublicId(row)]),
            title: String(readPath(row, label) ?? rowPublicId(row)),
            start: date, end: date, allDay: markerMetadata.fields[markerSpec.date]?.scalar === "Date",
            resourceId, color: toneColorVar(tone), readOnly: true,
          });
        }
      }
      return { resources, events, detailsByLane, skipped, error: null };
    } catch (cause) {
      return { resources: [], events: [], detailsByLane: new Map<string, GanttLaneDetails>(), skipped: records.skipped, error: errorFromUnknown(cause) };
    }
  }, [metadata, tableRows, laneSource, lanes, catalogue.rows, gantt.current, lane, records.skipped, markers.rows, markers.skipped, markerSpec, markerMetadata, startField, endField, toneField, label, resolveTone, t]);
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
    const details = projection.detailsByLane.get(resource.id);
    return <GanttLane details={details ?? { title: resource.title }} onNavigate={(href) => void navigate({ to: href })} />;
  }, [projection.detailsByLane, navigate]);
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
        fitToEvents={resourceView.state.anchor === calendarDateToAnchor(new Date())} sidebarWidth={gantt.sidebarWidth ?? (lane ? 280 : undefined)} minRowHeight={gantt.minRowHeight ?? (lane ? 4.5 : undefined)}
        laneHeader={laneMetadata?.pluralLabel ?? laneMetadata?.label}
        renderRowContent={lane ? renderResourceContent : undefined}
        onEventClick={onRowClick || rowHref ? handleEventClick : undefined}
      />
    </ResourceListFrame>
  );
}
