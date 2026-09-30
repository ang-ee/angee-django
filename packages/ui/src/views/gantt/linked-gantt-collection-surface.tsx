import * as React from "react";
import { ResourceQuery, rowPublicId, useModelMetadata, type Row } from "@angee/metadata";
import { MAX_PAGE_SIZE } from "@angee/refine";
import { useNavigate } from "@tanstack/react-router";
import { useUiT } from "../../i18n";
import { useValueStable } from "../../lib/use-value-stable";
import { useStatusTone } from "../../widgets/use-status-tone";
import { relationValueId } from "../../widgets/types";
import { errorFromUnknown } from "../../data/errors";
import { ResourceListFrame } from "../resource/ResourceListFrame";
import { useResourceToolbarProps } from "../resource/resource-toolbar-props";
import { useListViewToolbarInputs, type ListViewToolbarInputsProps } from "../resource/resource-view-toolbar-inputs";
import { readPath } from "../resource/resource-view-list-body";
import { listResultFromPageState, useResourceRowsSnapshot, useResourceViewQueryFacts } from "../resource/surface/table-state";
import { useResourceListQuery } from "../resource/surface/resource-list-query";
import type { UseResourceViewSurfaceProps } from "../resource/resource-view-surface";
import type { GanttViewSpec, ListViewProps } from "../resource/resource-view-types";
import { calendarAnchorToDate, calendarDateToAnchor } from "../calendar/calendar-view-controls";
import { GanttView, type GanttEvent, type GanttResource } from "./GanttView";
import { ganttLaneFilter, useGanttRecords } from "./gantt-collection-query";
import { ganttBarEvent } from "./gantt-bar-event";
import { GanttLane, withGanttLaneNotes, type GanttLaneDetails } from "./gantt-lane";

interface LinkedGanttCollectionSurfaceProps<TRow extends Row> extends Pick<ListViewProps<TRow>,
  "availableViews" | "onCreate" | "createLabel" | "toolbarActions" | "className" | "presentation" | "toolbarWrap" | "onRowClick" | "rowHref" | "maxGroupDepth" | "selectable"> {
  surfaceProps: UseResourceViewSurfaceProps<TRow>;
  gantt: GanttViewSpec;
  toolbarInputs: Omit<ListViewToolbarInputsProps<TRow>, "rows" | "list">;
}

/** The list resource owns the lane page and view state; the linked resource supplies bars only. */
export function LinkedGanttCollectionSurface<TRow extends Row>({
  surfaceProps, gantt, toolbarInputs: input, availableViews, onCreate, createLabel,
  toolbarActions, className, presentation, toolbarWrap, maxGroupDepth, onRowClick, rowHref, selectable,
}: LinkedGanttCollectionSurfaceProps<TRow>) {
  const t = useUiT();
  const navigate = useNavigate();
  const resolveTone = useStatusTone();
  const { resourceView, modelMetadata, columns, onListStateChange } = surfaceProps;
  const linked = gantt.linked;
  if (!linked) throw new Error("Linked Gantt requires a linked resource declaration.");
  const laneResource = modelMetadata?.resource ?? null;
  const linkedMetadata = useModelMetadata(linked.resource);
  const barResource = linkedMetadata?.resource ?? null;
  const laneQuery = React.useMemo(() => laneResource ? ResourceQuery.from(laneResource) : null, [laneResource]);
  const barQuery = React.useMemo(() => barResource ? ResourceQuery.from(barResource) : null, [barResource]);
  const laneLabel = laneResource?.recordRepresentation ?? "id";
  const barLabel = gantt.label ?? barResource?.recordRepresentation ?? "id";
  const currentPaths = React.useMemo(() => {
    if (!gantt.current || !laneQuery) return [];
    const field = laneQuery.fields[gantt.current];
    if (!field?.row) throw new Error(`Gantt current field "${gantt.current}" has no lane value projection.`);
    return field.row.paths;
  }, [gantt.current, laneQuery]);
  const laneFields = useValueStable([...(surfaceProps.fields ?? []), laneLabel, ...currentPaths, ...(gantt.lane?.fields ?? [])]);
  const laneFacts = useResourceViewQueryFacts({ columns, fields: laneFields, order: surfaceProps.order, resourceView, modelMetadata });
  const pageSize = Math.min(MAX_PAGE_SIZE, resourceView.state.pagination.pageSize);
  const scope = React.useMemo(() => ({ filter: laneFacts.mergedFilter, order: laneFacts.sortOrder,
    page: resourceView.state.pagination.pageIndex + 1, pageSize }),
  [laneFacts.mergedFilter, laneFacts.sortOrder, resourceView.state.pagination.pageIndex, pageSize]);
  const lanePage = useResourceListQuery({ resource: laneResource, scope, fields: laneFacts.requestedFields });
  const rows = lanePage.result.data as TRow[];
  const laneIds = React.useMemo(() => rows.map((row) => rowPublicId(row, laneResource)).filter((id): id is string => Boolean(id)), [rows, laneResource]);
  const barSource = React.useMemo(() => {
    try {
      if (!barQuery || !laneQuery) return { fields: [], filter: undefined, error: null };
      if (barQuery.relation(linked.lane)?.model !== laneResource?.modelLabel) {
        throw new Error(`Gantt linked field "${linked.lane}" must reference "${laneResource?.modelLabel}".`);
      }
      return {
        fields: [barQuery.contract.identity.field, gantt.start, gantt.end, barLabel, ...(gantt.tone ? [gantt.tone] : []), ...barQuery.selection([{ field: linked.lane }])],
        filter: laneIds.length ? ganttLaneFilter(barQuery, linked.lane, laneIds, linked.filter) : undefined,
        error: null,
      };
    } catch (cause) {
      return { fields: [], filter: undefined, error: errorFromUnknown(cause) };
    }
  }, [barQuery, laneQuery, linked, gantt.start, gantt.end, gantt.tone, barLabel, laneIds, laneResource?.modelLabel]);
  const barFields = useValueStable(barSource.fields);
  const bars = useGanttRecords({ resource: barResource, fields: barFields, filter: barSource.filter,
    enabled: Boolean(barResource && laneIds.length && !barSource.error) });
  const fetching = lanePage.query.isFetching || bars.fetching;
  const error = barSource.error ?? lanePage.query.error ?? bars.error;
  const refetch = React.useCallback(() => { void lanePage.query.refetch(); bars.refetch(); }, [lanePage.query.refetch, bars.refetch]);
  const list = listResultFromPageState({ resourceView, rows, total: lanePage.result.total, pageSize, fetching, error, refetch });
  useResourceRowsSnapshot<TRow>(list, { onListStateChange, navigation: { filter: scope.filter, order: scope.order } });
  const projection = React.useMemo(() => {
    const laneSet = new Set(laneIds);
    const detailsByLane = new Map<string, GanttLaneDetails>(rows.flatMap((row) => {
      const id = rowPublicId(row, laneResource);
      return id && gantt.lane ? [[id, gantt.lane.content(row)] as const] : [];
    }));
    const resources: GanttResource[] = rows.flatMap((row) => {
      const id = rowPublicId(row, laneResource);
      return id ? [{ id, title: detailsByLane.get(id)?.title || String(readPath(row, laneLabel) ?? id) }] : [];
    });
    let skipped = bars.skipped;
    const events: GanttEvent[] = bars.rows.flatMap((bar) => {
      const id = rowPublicId(bar, barResource);
      const laneId = relationValueId(readPath(bar, linked.lane));
      if (!id || !laneSet.has(laneId)) { skipped += 1; return []; }
      const allDay = linkedMetadata?.fields[gantt.start]?.scalar === "Date" && linkedMetadata?.fields[gantt.end]?.scalar === "Date";
      const tone = gantt.tone ? resolveTone(String(readPath(bar, gantt.tone) ?? "")) : "brand";
      const current = rows.some((row) => rowPublicId(row, laneResource) === laneId && gantt.current && relationValueId(readPath(row, gantt.current)) === id);
      const event = ganttBarEvent(bar, { id, resourceId: laneId, start: gantt.start, end: gantt.end,
        label: barLabel, tone, dateOnly: allDay, current });
      if (!event && readPath(bar, gantt.start) != null && readPath(bar, gantt.end) != null) skipped += 1;
      return event ? [event] : [];
    });
    return { resources, events: withGanttLaneNotes(events, detailsByLane), detailsByLane, skipped };
  }, [rows, laneIds, laneLabel, laneResource, barResource, bars.rows, bars.skipped, linkedMetadata, gantt, linked, barLabel, resolveTone]);
  const toolbarInputs = useListViewToolbarInputs({ ...input, rows, list, serverGrouping: false });
  const toolbar = useResourceToolbarProps({ ...toolbarInputs, resourceView, availableViews, view: "gantt",
    groupStack: surfaceProps.groupStack ?? resourceView.state.groupStack, groupingEnabled: false,
    favorites: resourceView.savedFavorites, createLabel, onCreate, actions: toolbarActions,
    wrap: toolbarWrap, maxGroupDepth, pagerMaxPageSize: MAX_PAGE_SIZE });
  const anchor = React.useMemo(() => calendarAnchorToDate(resourceView.state.anchor), [resourceView.state.anchor]);
  const onDateChange = React.useCallback((date: Date) => resourceView.setAnchor(calendarDateToAnchor(date)), [resourceView.setAnchor]);
  const openLane = React.useCallback((id: string) => {
    const row = rows.find((candidate) => rowPublicId(candidate, laneResource) === id);
    if (!row) return;
    if (onRowClick) onRowClick(row);
    else if (rowHref) void navigate({ to: rowHref(row) });
  }, [rows, laneResource, onRowClick, rowHref, navigate]);
  const renderResourceContent = React.useCallback((resource: GanttResource) => {
    const row = rows.find((candidate) => rowPublicId(candidate, laneResource) === resource.id);
    if (!row) return null;
    const details = projection.detailsByLane.get(resource.id) ?? { title: resource.title };
    return <GanttLane details={{ ...details, href: details.href ?? rowHref?.(row) }}
      onOpen={onRowClick ? () => onRowClick(row) : undefined}
      onNavigate={(href) => void navigate({ to: href })} />;
  }, [rows, laneResource, projection.detailsByLane, rowHref, onRowClick, navigate]);
  const selectedIds = Object.keys(resourceView.state.rowSelection).filter((id) => resourceView.state.rowSelection[id]);
  return <ResourceListFrame toolbar={toolbar} presentation={presentation} className={className}
    error={list.error} onRetry={refetch} loadingFooter={fetching}
    selection={selectable === false ? undefined : { count: selectedIds.length, onClear: resourceView.clearSelectedIds }}
    summary={projection.skipped ? t("gantt.skipped", { count: projection.skipped }) : undefined}>
    <GanttView resources={projection.resources} events={projection.events} date={anchor} onDateChange={onDateChange}
      defaultScale="quarter" loading={fetching} fitToEvents={resourceView.state.anchor === calendarDateToAnchor(new Date())}
      laneHeader={modelMetadata?.pluralLabel ?? modelMetadata?.label}
      sidebarWidth={gantt.sidebarWidth ?? (gantt.lane ? 280 : undefined)} minRowHeight={gantt.minRowHeight ?? (gantt.lane ? 4.5 : undefined)}
      renderRowContent={gantt.lane || rowHref || onRowClick ? renderResourceContent : undefined}
      onResourceClick={onRowClick || rowHref ? (resource) => openLane(resource.id) : undefined}
      onEventClick={onRowClick || rowHref ? (event) => { if (event.resourceId) openLane(event.resourceId); } : undefined}
      selectedRows={selectedIds}
      onSelectedRowsChange={selectable === false ? undefined : (ids) => resourceView.setRowSelection(Object.fromEntries(ids.map((id) => [id, true])))}
    />
  </ResourceListFrame>;
}
