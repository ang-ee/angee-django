import * as React from "react";
import { MAX_PAGE_SIZE, useAngeeAggregate } from "@angee/refine";
import { ResourceQuery, modelLabelSegment, rowPublicId, canDeleteRecord, useModelMetadata } from "@angee/metadata";
import type { ModelFieldMetadata, Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { titleCase } from "../../../lib/titleCase";
import { BoardView } from "../BoardView";
import { GalleryView, useGalleryRows } from "../../GalleryView";
import { GroupedBoardBody } from "../board/grouped";
import { useSelectionPlaces, type ResourceViewContextValue } from "../resource-view-context";
import { type ResourceViewFilter, type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";
import { DeletePreviewDialog } from "../../tree/DeletePreviewDialog";
import { type GroupedResourceViewSurface, type ResourceViewSurface } from "../resource-view-surface";
import { GroupedListBody } from "../GroupedList";
import { FlatListBody, groupMeasuresFromColumns, hasuraMeasuresFromGroupMeasures, type FlatListBodyProps, type GroupMeasure } from "../resource-view-list-body";
import { ResourceListFrame } from "../ResourceListFrame";
import { listChromeState, type BoardCardSpec, type CardActionContext, type ListEmptyContent, type ListViewProps } from "../resource-view-types";
import { DeclaredBoardCardBody, DefaultBoardCardBody } from "../board/cards";
import { columnsWithMetadataDefaults } from "../model-metadata-defaults";
import { createLabelForResource } from "../resource-view-utils";
import type { ColumnDescriptor } from "../../page";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { useBulkDelete } from "../useBulkDelete";
import { useBulkAction } from "../bulk-action";
import { BulkArchiveActions } from "../archive";
import { BulkTrashActions } from "../trash";
import { recordRepresentationValue, titleText } from "../../form/form-view-model";
import { requireDataResource, useAggregateOperation } from "../resource-operations";
import { useResourceSearch } from "../search/use-resource-search";
import { useSearchCatalog } from "../search/catalog";
import type { ResourceToolbarProps } from "../../../toolbars";
import { PAGE_SIZE_OPTIONS } from "../page-size";
import { EMPTY_SELECTED_IDS } from "../resource-view-codecs";
import { ResourceViewUtilities } from "../resource-view-utilities";
import { PaneListBody } from "./pane-list-body";
import { selectedGroupsOf } from "../resource-view-grouped-model";
import { useValueStable } from "../../../lib/use-value-stable";
interface ListViewContentProps<TRow extends Row> {
  source?: ListViewProps<TRow>["source"];
  textFilterField?: string | null;
  maxGroupDepth?: number;
  toolbarWrap?: boolean;
  tableLayout: ListViewProps<TRow>["tableLayout"];
  headerVisibility: ListViewProps<TRow>["headerVisibility"];
  selectable: boolean;
  renderGroupLabel?: ListViewProps<TRow>["renderGroupLabel"];
  renderPaneRow?: ListViewProps<TRow>["renderPaneRow"];
  pane?: ListViewProps<TRow>["pane"];
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
  /** The list's group read applies `GroupSpec.repeated` (`ValidatedListViewBody`). */
  appliesRepeated: boolean;
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
  gallery: ListViewProps<TRow>["gallery"];
  renderCard: ListViewProps<TRow>["renderCard"];
  cardFrame: ListViewProps<TRow>["cardFrame"];
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
  selectable,
  renderGroupLabel,
  renderPaneRow,
  pane,
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
  appliesRepeated,
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
  gallery,
  renderCard,
  cardFrame,
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
    search: searchDeclaration,
    inferOptions: !source,
    serverGrouping: !clientRowModel,
    appliesRepeated,
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
  const resolvedChrome = listChromeState(presentation, chrome, { ...surface.list, queryDirty: resourceView.queryDirty });
  const selectedIds = selectable ? surface.selectedIds : EMPTY_SELECTED_IDS;
  // A card's checkbox toggles its row in the shared selection, keeping the row as its
  // snapshot; a grouped board's card also captures where its record shows.
  const toggleCardSelected = selectable ? resourceView.toggleSelectedId : undefined;
  const placement = useSelectionPlaces();
  const deselectSucceeded = React.useCallback((ids: readonly string[]) => {
    resourceView.setRowSelection((selection) => {
      const remaining = { ...selection };
      for (const id of ids) delete remaining[id];
      return remaining;
    });
  }, [resourceView.setRowSelection]);
  const bulk = useBulkAction(source ? null : modelMetadata?.resource, deselectSucceeded);
  const selectedRowsById = React.useMemo(() => new Map(resourceView.selectedRows.map((row) => [rowPublicId(row), row])),
    [resourceView.selectedRows]);
  const deleteIds = React.useMemo(() => new Set([...selectedIds].filter((id) =>
    canDeleteRecord(modelMetadata?.resource, selectedRowsById.get(id)))),
  [selectedIds, selectedRowsById, modelMetadata]);
  const bulkDelete = useBulkDelete(
    source ? "" : resource,
    deleteIds,
    bulk,
    resourceView.selectedRows,
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
  // The gallery draws the page's card, else its declared image/title/subtitle card,
  // else the board's column card (declared or the leading columns) in an inset frame.
  const columnCardBody = React.useCallback((row: TRow) =>
    <DefaultBoardCardBody columns={resolvedColumns} groupFields={NO_GROUP_FIELDS} modelMetadata={modelMetadata} row={row} />,
  [resolvedColumns, modelMetadata]);
  const galleryRenderCard = renderCard ?? (gallery ? undefined : boardCard ? boardCardBody : columnCardBody);
  const galleryFrame = cardFrame ?? (renderCard || gallery ? "flush" : "inset");
  const galleryRows = useGalleryRows(surface.rowModels);
  // A card is named by its record representation, as the form header and the board are.
  const representationLabel = React.useCallback(
    (row: TRow) => titleText(recordRepresentationValue(row, modelMetadata), ""), [modelMetadata]);
  const search = useResourceSearch({ resourceView, catalog, groupStack: effectiveGroupStack,
    groupingEnabled: !boardGroupingPinned, maxGroupDepth });
  const heading = chrome?.heading ?? (resolvedChrome.compact
    ? { label: modelMetadata?.pluralLabel ?? modelMetadata?.label ?? titleCase(modelLabelSegment(resource)) }
    : undefined);
  const visibleFields = resolvedChrome.columnChooser ? surface.visibleFields : [];
  const contributedUtilities = (
    <ResourceViewUtilities
      modelBacked={!source}
      value={{
        resource: modelMetadata?.resource.modelLabel ?? resource,
        filter: effectiveFilter,
        selectedIds,
        selectable,
        fields: resolvedColumns.flatMap((column) => column.field ? [column.field] : []),
        refresh: () => void surface.list.refetch(),
      }}
    />
  );
  // Every grouped list pages its root groups: the server surface by its root read, a
  // grouping client table by its top-level rows; the surface says which.
  const pagesGroups = surface.list.pageUnit === "groups";
  const toolbar: ResourceToolbarProps = {
    pane,
    sortOptions: resolvedColumns.flatMap((column) => column.field && column.sortable !== false
      ? [{ value: column.field, label: typeof column.header === "string" ? column.header : column.field }] : []),
    sorting: resourceView.state.sorting, onSortingChange: resourceView.setSorting,
    search, chrome: resolvedChrome, wrap: toolbarWrap, actions: toolbarActions,
    utilityActions: contributedUtilities, availableViews, pager: surface.list,
    view: resourceView.state.view, searchDeclaration, modelMetadata,
    createLabel: createLabel ?? createLabelForResource(resource, t, modelMetadata?.label),
    onCreate, onPageChange: resourceView.setPage, onPageSizeChange: resourceView.setPageSize,
    onViewChange: availableViews.length > 1 ? resourceView.setView : undefined,
    pagerSubject: pagesGroups ? t("pager.groups") : undefined,
    pagerTotalUnit: pagesGroups ? "groups" : undefined,
    pagerPageSizeOptions: clientRowModel ? undefined : PAGE_SIZE_OPTIONS,
    pagerMaxPageSize: clientRowModel ? undefined : MAX_PAGE_SIZE,
  };

  // The split exists only for a page's bulk actions over a selection. Only the
  // server-grouped surface (list or board) places its loaded rows in groups.
  const splitsSelection = Boolean(bulkActions) && selectable && selectedIds.size > 0;
  const groupedItems = surface.kind === "grouped" ? surface.groupedItems : null;
  const shown = surface.kind === "grouped" ? surface.shown : null;
  // Value-stable, so a bulk action's memo or effect re-runs only when the split changes.
  const selectedGroups = useValueStable(React.useMemo(() => splitsSelection
    ? selectedGroupsOf(selectedIds, effectiveGroupStack,
      groupedItems && shown ? { places: placement.places, items: groupedItems, shown } : null)
    : undefined, [splitsSelection, selectedIds, placement.places, effectiveGroupStack, groupedItems, shown]));
  const domainBulkActions = splitsSelection
    ? bulkActions?.(selectedIds, resourceView.clearSelectedIds, {
      filter: surface.mergedFilter,
      // Scope snapshots are shared Rows; narrow to the declared row type (see bulkActions).
      selectedRows: resourceView.selectedRows as readonly TRow[],
      selectedGroups,
    }) : null;
  const genericSelection = !source && modelMetadata ? {
    modelMetadata,
    selectedRows: resourceView.selectedRows,
    unprocessedCount: [...selectedIds].filter((id) => !selectedRowsById.has(id)).length,
    bulk,
  } : null;

  return (
    <ResourceListFrame
      heading={heading}
      className={className}
      presentation={presentation}
      compact={resolvedChrome.compact}
      toolbar={toolbar}
      selection={selectable ? {
        count: selectedIds.size,
        onClear: resourceView.clearSelectedIds,
        onDelete:
          !source && bulkDelete.canDelete
            ? bulkDelete.deleteInitiate
            : undefined,
        deletePending: bulkDelete.isPending,
        pending: bulk.pending,
        actions: <>
          {domainBulkActions}
          {genericSelection ? <>
            <BulkArchiveActions {...genericSelection} />
            <BulkTrashActions {...genericSelection} />
          </> : null}
        </>,
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
      {renderPaneRow ? (
        <PaneListBody surface={surface} renderRow={renderPaneRow} renderGroupLabel={renderGroupLabel}
          groupStack={effectiveGroupStack} emptyContent={emptyContent} toolbar={pane ? toolbar : undefined} />
      ) : resourceView.state.view === "gallery" ? (
        <GalleryView<TRow>
          rows={galleryRows.rows}
          getRowId={galleryRows.getRowId}
          imageField={gallery?.image}
          titleField={gallery?.title}
          cardLabel={representationLabel}
          subtitleField={gallery?.subtitle}
          renderCard={galleryRenderCard}
          cardFrame={galleryFrame}
          cardActions={cardActions || renderRowActions ? (row) => boardCardActions(row, cardActionContext) : undefined}
          cardHref={rowHref ? (row) => rowHref(row, surface.listState.navigationScope) : undefined}
          onCardClick={onRowClick}
          draggableRow={draggableRow}
          selectedIds={selectedIds}
          onToggleSelected={toggleCardSelected}
          fetching={surface.list.fetching}
          emptyContent={emptyContent}
        />
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
          cardFrame={cardFrame ?? "inset"}
          selectedIds={selectedIds}
          onToggleSelected={selectable ? placement.toggleSelectedId : undefined}
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
          visibleFields={visibleFields}
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
          selectedIds={selectedIds}
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
          selectedIds={selectedIds}
          onToggleSelected={toggleCardSelected}
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
          cardFrame={cardFrame}
          dragEnabled={surface.boardDragEnabled}
          rankField={surface.boardRankField}
          rankInputField={surface.boardRankInputField}
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
          visibleFields={visibleFields}
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
          visibleFields={visibleFields}
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

const NO_GROUP_FIELDS: ReadonlySet<string> = new Set();

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
