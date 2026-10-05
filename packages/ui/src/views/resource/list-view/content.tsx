import * as React from "react";
import { MAX_PAGE_SIZE, useAngeeAggregate } from "@angee/refine";
import { ResourceQuery, useModelMetadata } from "@angee/metadata";
import type { ModelFieldMetadata, Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { LoadingPanel } from "../../../fragments/LoadingPanel";
import { BoardView } from "../BoardView";
import { GroupedBoardBody } from "../board/grouped";
import { type ResourceViewContextValue } from "../resource-view-context";
import { type ResourceViewFilter, type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";
import { DeletePreviewDialog } from "../../tree/DeletePreviewDialog";
import { type GroupedResourceViewSurface, type ResourceViewSurface } from "../resource-view-surface";
import { GroupedListBody } from "../GroupedList";
import { FlatListBody, ListEmpty, groupMeasuresFromColumns, hasuraMeasuresFromGroupMeasures, type FlatListBodyProps, type GroupMeasure } from "../resource-view-list-body";
import { ResourceListFrame } from "../ResourceListFrame";
import type { BoardCardSpec, CardActionContext, ListEmptyContent, ListViewProps } from "../resource-view-types";
import { DeclaredBoardCardBody } from "../board/cards";
import { columnsWithMetadataDefaults } from "../model-metadata-defaults";
import { createLabelForResource } from "../resource-view-utils";
import type { ColumnDescriptor } from "../../page";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { useBulkDelete } from "../useBulkDelete";
import { requireDataResource, useAggregateOperation } from "../resource-operations";
import { useResourceSearch } from "../search/use-resource-search";
import { useSearchCatalog } from "../search/catalog";
import type { ResourceToolbarProps } from "../../../toolbars";
import { PAGE_SIZE_OPTIONS } from "../page-size";
import { ResourceViewUtilities } from "../resource-view-utilities";
interface ListViewContentProps<TRow extends Row> {
  source?: ListViewProps<TRow>["source"];
  textFilterField?: string | null;
  maxGroupDepth?: number;
  toolbarWrap?: boolean;
  tableLayout: ListViewProps<TRow>["tableLayout"];
  headerVisibility: ListViewProps<TRow>["headerVisibility"];
  selectable: ListViewProps<TRow>["selectable"];
  renderGroupLabel?: ListViewProps<TRow>["renderGroupLabel"];
  renderItem?: ListViewProps<TRow>["renderItem"];
  surface: ResourceViewSurface<TRow> | GroupedResourceViewSurface<TRow>;
  resource: string;
  resolvedColumns: readonly ColumnDescriptor<TRow>[];
  modelMetadata: ReturnType<typeof useModelMetadata>;
  resourceView: ResourceViewContextValue;
  availableViews: readonly ResourceViewKind[];
  effectiveGroupStack: readonly ResourceViewGroup[];
  effectiveFilter?: ResourceViewFilter;
  boardGroupingPinned: boolean;
  clientRowModel: boolean;
  serverGroupedMode: boolean;
  declaredFacets: ReturnType<typeof useRelationFacets>;
  scalarFacets: ReturnType<typeof useScalarFacets>;
  explicitGroupOptions: ListViewProps<TRow>["groupOptions"];
  explicitFilterOptions: ListViewProps<TRow>["filterOptions"];
  searchDeclaration: ListViewProps<TRow>["search"];
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
  boardCard?: BoardCardSpec;
  renderCard: ListViewProps<TRow>["renderCard"];
  emptyContent: ListEmptyContent;
  className: string | undefined;
  presentation: ListViewProps<TRow>["presentation"];
  chrome: ListViewProps<TRow>["chrome"];
}

export function ListViewContent<TRow extends Row = Row>({
  source,
  textFilterField: declaredTextField,
  maxGroupDepth,
  toolbarWrap,
  tableLayout = "auto",
  headerVisibility = "visible",
  selectable = true,
  renderGroupLabel,
  renderItem,
  surface,
  resource,
  resolvedColumns,
  modelMetadata,
  resourceView,
  availableViews,
  effectiveGroupStack,
  effectiveFilter,
  boardGroupingPinned,
  clientRowModel,
  serverGroupedMode,
  declaredFacets,
  scalarFacets,
  explicitGroupOptions,
  explicitFilterOptions,
  searchDeclaration,
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
  boardCard,
  renderCard,
  emptyContent,
  className,
  presentation,
  chrome,
}: ListViewContentProps<TRow>): React.ReactElement {
  const t = useUiT();
  const flatMeasures = React.useMemo(
    () => groupMeasuresFromColumns(resolvedColumns),
    [resolvedColumns],
  );
  const catalog = useSearchCatalog({
    query: source?.query,
    search: searchDeclaration, renderItem: Boolean(renderItem),
    inferOptions: !source,
    serverGrouping: !clientRowModel,
    columns: resolvedColumns,
    rows: surface.rows,
    modelMetadata,
    resourceView,
    defaultGroup,
    defaultGroups,
    groupOptions: explicitGroupOptions,
    declaredFacets,
    scalarFacets,
    filterOptions: explicitFilterOptions,
    customFilterFields: explicitCustomFilterFields,
    textFilterField: declaredTextField,
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
  const declaredCardColumns = React.useMemo(() => boardCard
    ? columnsWithMetadataDefaults(
        [boardCard.title, ...(boardCard.fields ?? []).slice(0, 4)].map((field) =>
          resolvedColumns.find((column) => column.field === field) ?? { field },
        ),
        modelMetadata,
      )
    : [], [boardCard, resolvedColumns, modelMetadata]);
  const boardCardBody = React.useCallback((row: TRow) =>
    <DeclaredBoardCardBody columns={declaredCardColumns} modelMetadata={modelMetadata} row={row} />,
  [declaredCardColumns, modelMetadata]);
  const resolvedRenderCard = renderCard ?? (boardCard ? boardCardBody : undefined);
  const contributedUtilities = (
    <ResourceViewUtilities
      modelBacked={!source}
      value={{
        resource: modelMetadata?.resource.modelLabel ?? resource,
        filter: effectiveFilter,
        selectedIds: surface.selectedIds,
        selectable,
        fields: resolvedColumns.flatMap((column) => column.field ? [column.field] : []),
        refresh: () => void surface.list.refetch(),
      }}
    />
  );
  const search = useResourceSearch({ resourceView, catalog, groupStack: effectiveGroupStack,
    groupingEnabled: !renderItem && !boardGroupingPinned, maxGroupDepth });
  const toolbar: ResourceToolbarProps = {
    search, chrome, wrap: toolbarWrap, actions: toolbarActions,
    utilityActions: contributedUtilities, availableViews, pager: surface.list,
    view: resourceView.state.view, searchDeclaration, modelMetadata,
    createLabel: createLabel ?? createLabelForResource(resource, t, modelMetadata?.label),
    onCreate, onPageChange: resourceView.setPage, onPageSizeChange: resourceView.setPageSize,
    onViewChange: availableViews.length > 1 ? resourceView.setView : undefined,
    pagerSubject: serverGroupedMode ? t("pager.groups") : undefined,
    pagerTotalUnit: serverGroupedMode ? "groups" : undefined,
    pagerPageSizeOptions: clientRowModel ? undefined : PAGE_SIZE_OPTIONS,
    pagerMaxPageSize: clientRowModel ? undefined : MAX_PAGE_SIZE,
  };

  return (
    <ResourceListFrame
      heading={chrome?.heading}
      className={className}
      presentation={presentation}
      toolbar={toolbar}
      selection={selectable ? {
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
      } : undefined}
      error={surface.list.error}
      onRetry={() => void surface.list.refetch()}
      summary={surface.list.summary}
      loadingFooter={
        !serverGroupedMode &&
        resourceView.state.view !== "board" &&
        surface.list.fetching &&
        surface.rowModels.length > 0
      }
      fetching={surface.list.fetching}
      hasRows={surface.rows.length > 0}
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
      {renderItem ? (
        surface.rowModels.length > 0 ? <ol
          start={(surface.list.page - 1) * surface.list.pageSize + 1}
          className="min-h-0 flex-1 list-decimal space-y-6 overflow-auto p-4 pl-10"
        >{surface.rowModels.map((row) => <li key={row.id}>{renderItem(row.original)}</li>)}</ol>
          : surface.list.fetching ? <LoadingPanel message={t("list.loading")} />
            : <ListEmpty className="p-6">{emptyContent}</ListEmpty>
      ) : surface.kind === "grouped" && resourceView.state.view === "board" ? (
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
          renderCard={resolvedRenderCard}
          fetching={surface.list.fetching}
          error={surface.list.error}
          emptyContent={emptyContent}
        />
      ) : surface.kind === "grouped" ? (
        <GroupedListBody
          tableLayout={tableLayout}
          headerVisibility={headerVisibility}
          selectable={selectable}
          renderGroupLabel={renderGroupLabel}
          table={surface.table}
          tableColumns={surface.tableColumns}
          visibleColumnCount={surface.visibleColumnCount}
          visibleFields={chrome?.columnChooser === false ? [] : surface.visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          resourceView={resourceView}
          measures={surface.measures}
          listItems={surface.groupedItems}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          footerAggregate={surface.footerAggregate}
          expandedKeys={surface.expandedKeys}
          toggleGroup={surface.toggleGroup}
          setGroupsExpanded={surface.setGroupsExpanded}
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
          renderCard={resolvedRenderCard}
          dragEnabled={surface.boardDragEnabled}
          rankField={surface.boardRankField}
          optimisticPlacementByRowId={surface.boardOptimisticPlacementByRowId}
          onCardMove={surface.onBoardCardMove}
          onCreateInLane={onCreateInLane}
        />
      ) : flatMeasures.length > 0 && !clientRowModel ? (
        <FlatListBodyWithAggregate
          tableLayout={tableLayout}
          headerVisibility={headerVisibility}
          selectable={selectable}
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
          visibleFields={chrome?.columnChooser === false ? [] : surface.visibleFields}
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
          tableLayout={tableLayout}
          headerVisibility={headerVisibility}
          selectable={selectable}
          columns={resolvedColumns}
          table={surface.table}
          rowModels={surface.rowModels}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          visibleColumnCount={surface.visibleColumnCount}
          allPageSelected={surface.allPageSelected}
          somePageSelected={surface.somePageSelected}
          onPageSelectionChange={surface.setPageSelection}
          visibleFields={chrome?.columnChooser === false ? [] : surface.visibleFields}
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
