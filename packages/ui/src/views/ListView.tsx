import * as React from "react";
import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
  stableSerialize,
  useAngeeAggregate,
} from "@angee/refine";
import {
  isClientRowModel,
  useModelMetadata,
  useSchemaFieldMetadata,
} from "@angee/metadata";
import type {
  Row,
} from "@angee/metadata";

import { useUiT } from "../i18n";
import { BoardView } from "./BoardView";
import {
  withResourceViewScope,
  useResourceViewMaybe,
  type ResourceViewContextValue,
} from "./resource-view-context";
import {
  Filter,
  availableResourceViewKinds,
  type ResourceViewGroup,
  type ResourceViewKind,
} from "./resource-view-model";
import { CalendarCollectionSurface } from "./calendar/calendar-collection-surface";
import { DeletePreviewDialog } from "./tree/DeletePreviewDialog";
import {
  useClientResourceViewSurface,
  useGroupedResourceViewSurface,
  useResourceViewSurface,
  type GroupedResourceViewSurface,
  type ResourceViewSurface,
  type UseResourceViewSurfaceProps,
} from "./resource-view-surface";
import type { ResolvedBoardLaneSource } from "./resource-view-board-lanes";
import {
  GroupedListBody,
} from "./GroupedList";
import {
  FlatListBody,
  resourceViewGroupToAggregateDimension,
  groupMeasuresFromColumns,
  hasuraMeasuresFromGroupMeasures,
  type FlatListBodyProps,
  type GroupMeasure,
} from "./resource-view-list-body";
import { ResourceListFrame } from "./ResourceListFrame";
import type {
  CardActionContext,
  ListEmptyContent,
  ListViewProps,
} from "./resource-view-types";
import {
  createLabelForResource,
  mergeFilterFields,
  mergeFilterOptions,
  resolveResourceViewGroup,
  resolveTextFilterField,
} from "./resource-view-utils";
import { columnsWithMetadataDefaults, relationFieldInfo } from "./model-metadata-defaults";
import type { ColumnDescriptor } from "./page";
import { useRelationFacets } from "./relation/relation-facet";
import { useScalarFacets } from "./relation/scalar-facet";
import { useBulkDelete } from "./useBulkDelete";
import {
  requireDataResource,
  useAggregateOperation,
} from "./resource-operations";
import { useResourceToolbarProps } from "./resource-toolbar-props";
import {
  defaultGroupForView,
  useResourceViewToolbarInputs,
} from "./resource-view-toolbar-inputs";
import { useResourceViewGroupState } from "./resource-view-group-state";
import { useRowActionsSurface } from "./RowActions";

export type {
  ListViewNavigationScope,
  ResourceListSnapshot,
} from "./resource-view-surface";
export type {
  ColumnAlign,
  ListColumn,
} from "./resource-view-list-body";
export type {
  BoardLaneSource,
  CalendarViewSpec,
  CardActionContext,
  ListEmptyAction,
  ListEmptyContent,
  ListEmptyState,
  ListViewProps,
} from "./resource-view-types";

export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  return <ListViewFrame {...props} />;
}

function ListViewFrame<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  const resourceView = useResourceViewMaybe();
  const scope = props.scope ?? "inherit";
  const navigationScope = props.navigationScope;
  const resolvedProps = navigationScope
    ? {
        ...props,
        baseFilter:
          navigationScope.filter as ListViewProps<TRow>["baseFilter"],
        order: navigationScope.order as ListViewProps<TRow>["order"],
        pageSize: navigationScope.pageSize,
      }
    : props;
  const initialState = React.useMemo(
    () => ({
      page: navigationScope?.page,
      pageSize: resolvedProps.pageSize,
      view: props.defaultView,
    }),
    [navigationScope?.page, props.defaultView, resolvedProps.pageSize],
  );
  return withResourceViewScope({
    ambient: resourceView,
    resource: props.resource,
    scope,
    initialState,
    isolated: navigationScope !== undefined,
    providerKey: navigationScope ? stableSerialize(navigationScope) : undefined,
    children: (scopedResourceView) => (
      <ListViewBody {...resolvedProps} resourceView={scopedResourceView} />
    ),
  });
}

function ListViewBody<TRow extends Row = Row>({
  resource,
  columns,
  fields,
  baseFilter,
  filterOptions: explicitFilterOptions,
  facets,
  customFilterFields: explicitCustomFilterFields,
  groupOptions: explicitGroupOptions,
  order,
  defaultGroup,
  defaultGroups,
  calendar,
  laneSource,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  rowActions,
  draggableRow,
  toolbarActions,
  bulkActions,
  cardActions,
  renderCard,
  emptyContent,
  className,
  resourceView,
}: ListViewProps<TRow> & {
  resourceView: ResourceViewContextValue;
}): React.ReactElement {
  const t = useUiT();
  const rowActionSurface = useRowActionsSurface(rowActions);
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  // The Calendar kind is offered only where the page declares occurrence sources;
  // the switcher's options derive from that (list + board always).
  const calendarAvailable = (calendar?.sources.length ?? 0) > 0;
  const availableViews = React.useMemo(
    () => availableResourceViewKinds({ calendar: calendarAvailable }),
    [calendarAvailable],
  );
  const modelMetadata = useModelMetadata(resource);
  const schemaMetadata = useSchemaFieldMetadata();
  const resolvedLaneSource = React.useMemo<ResolvedBoardLaneSource | null>(() => {
    if (!laneSource) return null;
    const fieldMetadata = modelMetadata?.fields[laneSource.field];
    const relation = relationFieldInfo(laneSource.field, modelMetadata, schemaMetadata);
    if (!relation) {
      if (modelMetadata) {
        throw new Error(
          `ListView laneSource field "${laneSource.field}" must resolve to a relation.`,
        );
      }
      return null;
    }
    if (!fieldMetadata) return null;
    return { ...laneSource, relation, fieldMetadata };
  }, [laneSource, modelMetadata, schemaMetadata]);
  const resolvedColumns = React.useMemo(
    () => columnsWithMetadataDefaults(columns, modelMetadata, schemaMetadata),
    [columns, modelMetadata, schemaMetadata],
  );
  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(baseFilter, resourceView.state.filter),
    [resourceView.state.filter, baseFilter],
  );
  const declaredFacets = useRelationFacets(resource, facets, mergedFilter);
  const scalarFacets = useScalarFacets(
    resource,
    resolvedColumns,
    modelMetadata,
    mergedFilter,
  );
  const laneSourceGroup = React.useMemo(
    () =>
      resolvedLaneSource
        ? resolveResourceViewGroup({ field: resolvedLaneSource.field }, modelMetadata)
        : null,
    [modelMetadata, resolvedLaneSource],
  );
  const boardGroupingPinned =
    resourceView.state.view === "board" && laneSourceGroup !== null;
  const rawActiveDefaultGroup = boardGroupingPinned
    ? laneSourceGroup
    : defaultGroupForView(
        defaultGroup,
        defaultGroups,
        resourceView.state.view,
      );
  const effectiveGroupStack = useResourceViewGroupState({
    resourceView,
    defaultGroup: rawActiveDefaultGroup,
    modelMetadata,
    pinned: boardGroupingPinned,
  });

  // A client resource holds the whole set in the browser, so it groups through
  // TanStack row models — never the server _groups/GroupedListBody path (the
  // aggregate it would query does not exist).
  const clientRowModel = isClientRowModel(modelMetadata?.resource);
  const groupDimensions = React.useMemo(
    () =>
      clientRowModel
        ? []
        : effectiveGroupStack.map((group) =>
            resourceViewGroupToAggregateDimension(group, modelMetadata),
          ),
    [clientRowModel, effectiveGroupStack, modelMetadata],
  );
  const groupedListMode =
    resourceView.state.view === "list"
    && groupDimensions.length > 0
    && !clientRowModel;
  const surfaceProps: UseResourceViewSurfaceProps<TRow> = {
    resource,
    columns: resolvedColumns,
    fields,
    filter: baseFilter,
    order,
    resourceView,
    modelMetadata,
    groupStack: effectiveGroupStack,
    laneSource: resolvedLaneSource,
    enabled: !groupedListMode,
    onListStateChange,
  };
  const content = (
    surface: ResourceViewSurface<TRow> | GroupedResourceViewSurface<TRow>,
  ) => (
    <ListViewContent<TRow>
      surface={surface}
      resource={resource}
      resolvedColumns={resolvedColumns}
      modelMetadata={modelMetadata}
      resourceView={resourceView}
      availableViews={availableViews}
      effectiveGroupStack={effectiveGroupStack}
      boardGroupingPinned={boardGroupingPinned}
      clientRowModel={clientRowModel}
      groupedListMode={groupedListMode}
      declaredFacets={declaredFacets}
      scalarFacets={scalarFacets}
      explicitGroupOptions={explicitGroupOptions}
      explicitFilterOptions={explicitFilterOptions}
      explicitCustomFilterFields={explicitCustomFilterFields}
      defaultGroup={defaultGroup}
      defaultGroups={defaultGroups}
      onCreate={onCreate}
      createLabel={createLabel}
      onRowClick={onRowClick}
      onListStateChange={onListStateChange}
      rowHref={rowHref}
      renderRowActions={
        rowActionSurface.hasActions ? rowActionSurface.render : undefined
      }
      draggableRow={draggableRow}
      toolbarActions={toolbarActions}
      bulkActions={bulkActions}
      cardActions={cardActions}
      renderCard={renderCard}
      emptyContent={resolvedEmptyContent}
      className={className}
    />
  );
  // A client resource fetches once and pages in the browser; a server resource
  // queries Hasura per page; the calendar fetches a window over authored sources.
  // Each data path calls different hooks, so the choice is a component boundary
  // (never a conditional hook): a view/metadata flip remounts the matching surface
  // rather than reordering hooks. The calendar surface never calls `useList`.
  if (calendar && resourceView.state.view === "calendar" && calendarAvailable) {
    return (
      <CalendarCollectionSurface
        resource={resource}
        resourceView={resourceView}
        calendar={calendar}
        availableViews={availableViews}
        createLabel={createLabel}
        onCreate={onCreate}
        toolbarActions={toolbarActions}
        className={className}
      />
    );
  }
  if (clientRowModel) {
    return <ClientSurfaceBody<TRow> surfaceProps={surfaceProps}>{content}</ClientSurfaceBody>;
  }
  if (groupedListMode) {
    return <GroupedServerSurfaceBody<TRow> surfaceProps={surfaceProps}>{content}</GroupedServerSurfaceBody>;
  }
  return <ServerSurfaceBody<TRow> surfaceProps={surfaceProps}>{content}</ServerSurfaceBody>;
}

interface SurfaceBodyProps<TRow extends Row> {
  surfaceProps: UseResourceViewSurfaceProps<TRow>;
  children: (surface: ResourceViewSurface<TRow>) => React.ReactElement;
}

interface GroupedSurfaceBodyProps<TRow extends Row> {
  surfaceProps: UseResourceViewSurfaceProps<TRow>;
  children: (surface: GroupedResourceViewSurface<TRow>) => React.ReactElement;
}

function ServerSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useResourceViewSurface(surfaceProps));
}

function GroupedServerSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: GroupedSurfaceBodyProps<TRow>): React.ReactElement {
  return children(useGroupedResourceViewSurface(surfaceProps));
}

function ClientSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useClientResourceViewSurface(surfaceProps));
}

interface ListViewContentProps<TRow extends Row> {
  surface: ResourceViewSurface<TRow> | GroupedResourceViewSurface<TRow>;
  resource: string;
  resolvedColumns: readonly ColumnDescriptor<TRow>[];
  modelMetadata: ReturnType<typeof useModelMetadata>;
  resourceView: ResourceViewContextValue;
  availableViews: readonly ResourceViewKind[];
  effectiveGroupStack: readonly ResourceViewGroup[];
  boardGroupingPinned: boolean;
  clientRowModel: boolean;
  groupedListMode: boolean;
  declaredFacets: ReturnType<typeof useRelationFacets>;
  scalarFacets: ReturnType<typeof useScalarFacets>;
  explicitGroupOptions: ListViewProps<TRow>["groupOptions"];
  explicitFilterOptions: ListViewProps<TRow>["filterOptions"];
  explicitCustomFilterFields: ListViewProps<TRow>["customFilterFields"];
  defaultGroup: ListViewProps<TRow>["defaultGroup"];
  defaultGroups: ListViewProps<TRow>["defaultGroups"];
  onCreate: ListViewProps<TRow>["onCreate"];
  createLabel: ListViewProps<TRow>["createLabel"];
  onRowClick: ListViewProps<TRow>["onRowClick"];
  onListStateChange: ListViewProps<TRow>["onListStateChange"];
  rowHref: ListViewProps<TRow>["rowHref"];
  renderRowActions: ((row: TRow) => React.ReactNode) | undefined;
  draggableRow: ListViewProps<TRow>["draggableRow"];
  toolbarActions: ListViewProps<TRow>["toolbarActions"];
  bulkActions: ListViewProps<TRow>["bulkActions"];
  cardActions: ListViewProps<TRow>["cardActions"];
  renderCard: ListViewProps<TRow>["renderCard"];
  emptyContent: ListEmptyContent;
  className: string | undefined;
}

function ListViewContent<TRow extends Row = Row>({
  surface,
  resource,
  resolvedColumns,
  modelMetadata,
  resourceView,
  availableViews,
  effectiveGroupStack,
  boardGroupingPinned,
  clientRowModel,
  groupedListMode,
  declaredFacets,
  scalarFacets,
  explicitGroupOptions,
  explicitFilterOptions,
  explicitCustomFilterFields,
  defaultGroup,
  defaultGroups,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  renderRowActions,
  draggableRow,
  toolbarActions,
  bulkActions,
  cardActions,
  renderCard,
  emptyContent,
  className,
}: ListViewContentProps<TRow>): React.ReactElement {
  const t = useUiT();
  const flatMeasures = React.useMemo(
    () => groupMeasuresFromColumns(resolvedColumns),
    [resolvedColumns],
  );
  const facetFilters = React.useMemo(
    () => mergeFilterOptions(declaredFacets.filters, scalarFacets.filters),
    [declaredFacets.filters, scalarFacets.filters],
  );
  const facetCustomFilterFields = React.useMemo(
    () => mergeFilterFields(declaredFacets.filterFields, scalarFacets.filterFields),
    [declaredFacets.filterFields, scalarFacets.filterFields],
  );
  // Search the model's real title field (recordRepresentation → e.g. displayName
  // for Person), not the hardcoded "title" that non-title models lack.
  const textFilterField = resolveTextFilterField(modelMetadata);
  const toolbarInputs = useResourceViewToolbarInputs({
    columns: resolvedColumns,
    rows: surface.rows,
    modelMetadata,
    resourceView,
    list: surface.list,
    defaultGroup,
    defaultGroups,
    groupOptions: explicitGroupOptions,
    contributedGroupOptions: declaredFacets.groupOptions,
    filterOptions: explicitFilterOptions,
    contributedFilterOptions: facetFilters,
    customFilterFields: explicitCustomFilterFields,
    contributedCustomFilterFields: facetCustomFilterFields,
    textFilterField,
    groupStack: effectiveGroupStack,
  });
  const interactive = Boolean(onRowClick || rowHref);
  const bulkDelete = useBulkDelete(
    resource,
    surface.selectedIds,
    resourceView.clearSelectedIds,
  );
  const cardActionContext = React.useMemo(
    () => ({ refresh: surface.list.refetch }),
    [surface.list.refetch],
  );
  const boardCardActions = React.useCallback(
    (row: TRow, context: CardActionContext) => {
      const pageActions = cardActions?.(row, context);
      const declaredActions = renderRowActions?.(row);
      if (!pageActions && !declaredActions) return null;
      return (
        <>
          {pageActions}
          {declaredActions}
        </>
      );
    },
    [cardActions, renderRowActions],
  );
  const toolbar = useResourceToolbarProps({
    actions: toolbarActions,
    availableViews,
    pager: toolbarInputs.pager,
    view: resourceView.state.view,
    group: effectiveGroupStack[0] ?? null,
    groupStack: effectiveGroupStack,
    groupOptions: toolbarInputs.groupOptions,
    filterOptions: toolbarInputs.filterOptions,
    customFilterFields: toolbarInputs.customFilterFields,
    customFilterChips: toolbarInputs.customFilterChips,
    favorites: resourceView.savedFavorites,
    activeFilterIds: toolbarInputs.activeFilterIds,
    filterText: toolbarInputs.filterText,
    textFilterField,
    createLabel: createLabel ?? createLabelForResource(resource),
    onCreate,
    resourceView,
    groupingEnabled: !boardGroupingPinned,
    pagerSubject: groupedListMode ? t("pager.groups") : undefined,
    pagerTotalUnit: groupedListMode ? "groups" : undefined,
  });

  return (
    <ResourceListFrame
      className={className}
      toolbar={toolbar}
      selection={{
        count: surface.selectedIds.size,
        onClear: resourceView.clearSelectedIds,
        onDelete:
          !bulkActions && bulkDelete.canDelete
            ? bulkDelete.deleteInitiate
            : undefined,
        deletePending: !bulkActions && bulkDelete.isPending,
        actions:
          bulkActions && surface.selectedIds.size > 0
            ? bulkActions(surface.selectedIds, resourceView.clearSelectedIds)
            : undefined,
      }}
      error={groupedListMode ? null : surface.list.error}
      loadingFooter={
        !groupedListMode
        && resourceView.state.view !== "board"
        && surface.list.fetching
        && surface.rowModels.length > 0
      }
      overlays={
        bulkDelete.isPreviewOpen && bulkDelete.previewState ? (
          <DeletePreviewDialog
            preview={bulkDelete.previewState}
            recordCount={bulkDelete.previewRecordCount}
            blockedRecordCount={bulkDelete.previewBlockedRecordCount}
            overflowCount={bulkDelete.previewOverflowCount}
            isPending={bulkDelete.isPending}
            onConfirm={bulkDelete.onConfirm}
            onCancel={bulkDelete.onCancel}
          />
        ) : null
      }
    >
      {surface.kind === "grouped" ? (
        <GroupedListBody
          columns={resolvedColumns}
          table={surface.table}
          tableColumns={surface.tableColumns}
          visibleColumnCount={surface.visibleColumnCount}
          visibleFields={surface.visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          resourceView={resourceView}
          modelMetadata={modelMetadata}
          listItems={surface.groupedItems}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          footerAggregate={surface.footerAggregate}
          expandedKeys={surface.expandedKeys}
          toggleGroup={surface.toggleGroup}
          setScopePage={surface.setScopePage}
          selectedIds={surface.selectedIds}
          interactive={interactive}
          rowHref={rowHref}
          renderRowActions={renderRowActions}
          onRowClick={onRowClick}
          draggableRow={draggableRow}
          onListStateChange={onListStateChange}
          emptyContent={emptyContent}
          fetching={surface.list.fetching}
          error={surface.list.error}
        />
      ) : resourceView.state.view === "board" ? (
        <BoardView
          columns={resolvedColumns}
          groups={surface.groupedRows}
          resourceView={resourceView}
          modelMetadata={modelMetadata}
          selectedIds={surface.selectedIds}
          interactive={interactive}
          fetching={surface.list.fetching}
          emptyContent={emptyContent}
          rowHref={rowHref}
          onRowClick={onRowClick}
          cardActions={
            cardActions || renderRowActions ? boardCardActions : undefined
          }
          cardActionContext={cardActionContext}
          renderCard={renderCard}
          dragEnabled={surface.boardDragEnabled}
          onCardMove={surface.onBoardCardMove}
        />
      ) : flatMeasures.length > 0 && !clientRowModel ? (
        <FlatListBodyWithAggregate
          resource={resource}
          filter={surface.mergedFilter}
          modelMetadata={modelMetadata}
          measures={flatMeasures}
          columns={resolvedColumns}
          table={surface.table}
          rowModels={surface.rowModels}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          visibleColumnCount={surface.visibleColumnCount}
          allPageSelected={surface.allPageSelected}
          somePageSelected={surface.somePageSelected}
          onPageSelectionChange={surface.setPageSelection}
          visibleFields={surface.visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          resourceView={resourceView}
          groupStack={effectiveGroupStack}
          interactive={interactive}
          rowHref={rowHref}
          renderRowActions={renderRowActions}
          onRowClick={onRowClick}
          emptyContent={emptyContent}
          fetching={surface.list.fetching}
          draggableRow={draggableRow}
        />
      ) : (
        <FlatListBody
          columns={resolvedColumns}
          table={surface.table}
          rowModels={surface.rowModels}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          visibleColumnCount={surface.visibleColumnCount}
          allPageSelected={surface.allPageSelected}
          somePageSelected={surface.somePageSelected}
          onPageSelectionChange={surface.setPageSelection}
          visibleFields={surface.visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          resourceView={resourceView}
          groupStack={effectiveGroupStack}
          interactive={interactive}
          rowHref={rowHref}
          renderRowActions={renderRowActions}
          onRowClick={onRowClick}
          emptyContent={emptyContent}
          fetching={surface.list.fetching}
          draggableRow={draggableRow}
        />
      )}
    </ResourceListFrame>
  );
}

function FlatListBodyWithAggregate<TRow extends Row>({
  resource,
  filter,
  modelMetadata,
  measures,
  ...props
}: FlatListBodyProps<TRow> & {
  resource: string;
  filter: Record<string, unknown> | undefined;
  modelMetadata: ReturnType<typeof useModelMetadata>;
  measures: readonly GroupMeasure[];
}): React.ReactElement {
  const dataResource = requireDataResource(resource, modelMetadata);
  const aggregateOperation = useAggregateOperation(dataResource);
  const where = React.useMemo(
    () => hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(filter)),
    [filter],
  );
  const queryMeasures = React.useMemo(
    () => hasuraMeasuresFromGroupMeasures(measures, modelMetadata),
    [measures, modelMetadata],
  );
  const aggregate = useAngeeAggregate(aggregateOperation.target, {
    document: aggregateOperation.document,
    where,
    measures: queryMeasures,
    enabled: queryMeasures.length > 0,
  });
  return <FlatListBody {...props} footerAggregate={aggregate.aggregate} />;
}
