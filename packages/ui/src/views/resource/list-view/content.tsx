import * as React from "react";
import { MAX_PAGE_SIZE, useAngeeAggregate } from "@angee/refine";
import { ResourceQuery, useModelMetadata } from "@angee/metadata";
import type { ModelFieldMetadata, Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { BoardView } from "../BoardView";
import { GroupedBoardBody } from "../board/grouped";
import { type ResourceViewContextValue } from "../resource-view-context";
import { type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";
import { DeletePreviewDialog } from "../../tree/DeletePreviewDialog";
import { type GroupedResourceViewSurface, type ResourceViewSurface } from "../resource-view-surface";
import { GroupedListBody } from "../GroupedList";
import { FlatListBody, groupMeasuresFromColumns, hasuraMeasuresFromGroupMeasures, type FlatListBodyProps, type GroupMeasure } from "../resource-view-list-body";
import { ResourceListFrame } from "../ResourceListFrame";
import type { CardActionContext, ListEmptyContent, ListViewProps } from "../resource-view-types";
import { createLabelForResource, mergeFilterFields, mergeFilterOptions, resolveTextFilterField } from "../resource-view-utils";
import type { ColumnDescriptor } from "../../page";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { useBulkDelete } from "../useBulkDelete";
import { requireDataResource, useAggregateOperation } from "../resource-operations";
import { useResourceToolbarProps } from "../resource-toolbar-props";
import { useResourceViewToolbarInputs } from "../resource-view-toolbar-inputs";
import { PAGE_SIZE_OPTIONS } from "../page-size";
interface ListViewContentProps<TRow extends Row> {
  source?: ListViewProps<TRow>["source"];
  textFilterField?: string | null;
  maxGroupDepth?: number;
  toolbarWrap?: boolean;
  renderGroupLabel?: ListViewProps<TRow>["renderGroupLabel"];
  surface: ResourceViewSurface<TRow> | GroupedResourceViewSurface<TRow>;
  resource: string;
  resolvedColumns: readonly ColumnDescriptor<TRow>[];
  modelMetadata: ReturnType<typeof useModelMetadata>;
  resourceView: ResourceViewContextValue;
  availableViews: readonly ResourceViewKind[];
  effectiveGroupStack: readonly ResourceViewGroup[];
  boardGroupingPinned: boolean;
  clientRowModel: boolean;
  serverGroupedMode: boolean;
  declaredFacets: ReturnType<typeof useRelationFacets>;
  scalarFacets: ReturnType<typeof useScalarFacets>;
  explicitGroupOptions: ListViewProps<TRow>["groupOptions"];
  explicitFilterOptions: ListViewProps<TRow>["filterOptions"];
  explicitCustomFilterFields: ListViewProps<TRow>["customFilterFields"];
  defaultGroup: ListViewProps<TRow>["defaultGroup"];
  defaultGroups: ListViewProps<TRow>["defaultGroups"];
  onCreate: ListViewProps<TRow>["onCreate"];
  onCreateInLane: ListViewProps<TRow>["onCreateInLane"];
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

export function ListViewContent<TRow extends Row = Row>({
  source,
  textFilterField: declaredTextField,
  maxGroupDepth,
  toolbarWrap,
  renderGroupLabel,
  surface,
  resource,
  resolvedColumns,
  modelMetadata,
  resourceView,
  availableViews,
  effectiveGroupStack,
  boardGroupingPinned,
  clientRowModel,
  serverGroupedMode,
  declaredFacets,
  scalarFacets,
  explicitGroupOptions,
  explicitFilterOptions,
  explicitCustomFilterFields,
  defaultGroup,
  defaultGroups,
  onCreate,
  onCreateInLane,
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
    () =>
      mergeFilterFields(declaredFacets.filterFields, scalarFacets.filterFields),
    [declaredFacets.filterFields, scalarFacets.filterFields],
  );
  // Search the model's real title field (recordRepresentation → e.g. displayName
  // for Person), not the hardcoded "title" that non-title models lack.
  const textFilterField =
    declaredTextField === undefined
      ? resolveTextFilterField(modelMetadata)
      : declaredTextField;
  const toolbarInputs = useResourceViewToolbarInputs({
    query: source?.query,
    inferOptions: !source,
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
    source ? "" : resource,
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
    maxGroupDepth,
    wrap: toolbarWrap,
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
    pagerSubject: serverGroupedMode ? t("pager.groups") : undefined,
    pagerTotalUnit: serverGroupedMode ? "groups" : undefined,
    pagerPageSizeOptions: clientRowModel ? undefined : PAGE_SIZE_OPTIONS,
    pagerMaxPageSize: clientRowModel ? undefined : MAX_PAGE_SIZE,
  });

  return (
    <ResourceListFrame
      className={className}
      toolbar={toolbar}
      selection={{
        count: surface.selectedIds.size,
        onClear: resourceView.clearSelectedIds,
        onDelete:
          !source && !bulkActions && bulkDelete.canDelete
            ? bulkDelete.deleteInitiate
            : undefined,
        deletePending: !bulkActions && bulkDelete.isPending,
        actions:
          bulkActions && surface.selectedIds.size > 0
            ? bulkActions(surface.selectedIds, resourceView.clearSelectedIds)
            : undefined,
      }}
      error={surface.list.error}
      onRetry={() => void surface.list.refetch()}
      summary={surface.list.summary}
      loadingFooter={
        !serverGroupedMode &&
        resourceView.state.view !== "board" &&
        surface.list.fetching &&
        surface.rowModels.length > 0
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
      {surface.kind === "grouped" && resourceView.state.view === "board" ? (
        <GroupedBoardBody
          columns={resolvedColumns}
          modelMetadata={modelMetadata}
          groupStack={effectiveGroupStack}
          items={surface.groupedItems}
          toggleGroup={surface.toggleGroup}
          setScopePage={surface.setScopePage}
          setScopePageSize={surface.setScopePageSize}
          rowHref={rowHref}
          onRowClick={onRowClick}
          onListStateChange={onListStateChange}
          cardActions={
            cardActions || renderRowActions ? boardCardActions : undefined
          }
          cardActionContext={cardActionContext}
          renderCard={renderCard}
          fetching={surface.list.fetching}
          error={surface.list.error}
          emptyContent={emptyContent}
        />
      ) : surface.kind === "grouped" ? (
        <GroupedListBody
          renderGroupLabel={renderGroupLabel}
          table={surface.table}
          tableColumns={surface.tableColumns}
          visibleColumnCount={surface.visibleColumnCount}
          visibleFields={surface.visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          resourceView={resourceView}
          measures={surface.measures}
          listItems={surface.groupedItems}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          footerAggregate={surface.footerAggregate}
          expandedKeys={surface.expandedKeys}
          toggleGroup={surface.toggleGroup}
          setScopePage={surface.setScopePage}
          setScopePageSize={surface.setScopePageSize}
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
          rowHref={
            rowHref
              ? (row) => rowHref(row, surface.listState.navigationScope)
              : undefined
          }
          onRowClick={onRowClick}
          cardActions={
            cardActions || renderRowActions ? boardCardActions : undefined
          }
          cardActionContext={cardActionContext}
          renderCard={renderCard}
          dragEnabled={surface.boardDragEnabled}
          rankField={surface.boardRankField}
          optimisticPlacementByRowId={surface.boardOptimisticPlacementByRowId}
          onCardMove={surface.onBoardCardMove}
          onCreateInLane={onCreateInLane}
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
          rowHref={
            rowHref
              ? (row) => rowHref(row, surface.listState.navigationScope)
              : undefined
          }
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
          rowHref={
            rowHref
              ? (row) => rowHref(row, surface.listState.navigationScope)
              : undefined
          }
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

export function isBoardRankField(
  field: ModelFieldMetadata | undefined,
): field is ModelFieldMetadata {
  return Boolean(
    field
    && field.kind === "scalar"
    && field.scalar === "Float"
    && field.readable !== false
    && field.nullable !== true,
  );
}

export function isBoardFoldField(
  field: ModelFieldMetadata | undefined,
): field is ModelFieldMetadata {
  return Boolean(
    field
    && field.kind === "scalar"
    && field.scalar === "Boolean"
    && field.readable !== false,
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
    () => ResourceQuery.from(dataResource).toWhere(filter),
    [filter, dataResource],
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
