import * as React from "react";
import { MAX_PAGE_SIZE, stableSerialize, useAngeeAggregate } from "@angee/refine";
import { ResourceQuery, modelLabelSegment, rowPublicId, canDeleteRecord, useModelMetadata } from "@angee/metadata";
import type { ModelFieldMetadata, Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { titleCase } from "../../../lib/titleCase";
import { BoardView } from "../BoardView";
import { GalleryView, useGalleryRows } from "../../GalleryView";
import { GroupedBoardBody } from "../board/grouped";
import type { ResourceViewContextValue } from "../resource-view-context";
import { useSelectionPlaces, type ShownSelection } from "../selection-channel";
import { type ResourceViewFilter, type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";
import { DeletePreviewDialog } from "../../tree/DeletePreviewDialog";
import { type GroupedResourceViewSurface, type ResourceViewSurface } from "../resource-view-surface";
import { GroupedListBody } from "../GroupedList";
import { FlatListBody, groupMeasuresFromColumns, hasuraMeasuresFromGroupMeasures, type FlatListBodyProps, type GroupMeasure } from "../resource-view-list-body";
import { listOrderChoice } from "../list-body/cell-utils";
import { ResourceListFrame } from "../ResourceListFrame";
import { listChromeState, type AllMatching, type BoardCardSpec, type CardActionContext, type ListEmptyContent, type ListViewProps } from "../resource-view-types";
import { DeclaredBoardCardBody, DefaultBoardCardBody } from "../board/cards";
import { columnsWithMetadataDefaults } from "../model-metadata-defaults";
import { createLabelForResource } from "../resource-view-utils";
import type { ColumnDescriptor } from "../../page";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { useBulkDelete } from "../useBulkDelete";
import { renderBulkActions, useBulkAction, type PlacedBulkActions } from "../bulk-action";
import { BulkArchiveActions } from "../archive";
import { BulkTrashActions } from "../trash";
import { recordRepresentationValue, titleText } from "../../form/form-view-model";
import { requireDataResource, useAggregateOperation } from "../resource-operations";
import { useResourceSearch } from "../search/use-resource-search";
import { useSearchCatalog } from "../search/catalog";
import type { ResourceToolbarProps } from "../../../toolbars";
import { PAGE_SIZE_OPTIONS } from "../page-size";
import { EMPTY_SELECTED_IDS } from "../resource-view-codecs";
import { EMPTY_ARRAY } from "../surface/types";
import { ResourceViewUtilities } from "../resource-view-utilities";
import { PaneListBody } from "./pane-list-body";
import { placedRecords, selectedGroupsOf } from "../resource-view-grouped-model";
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
  bulkActions: PlacedBulkActions<TRow> | undefined;
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
  // The page's href under this list's navigation scope: one function per scope, so a
  // render that changes neither (a scroll frame) hands every row and card the same one.
  const navigationScope = surface.listState.navigationScope;
  const scopedRowHref = React.useMemo(() => rowHref ? (row: TRow) => rowHref(row, navigationScope) : undefined,
    [rowHref, navigationScope]);
  const resolvedChrome = listChromeState(presentation, chrome, { ...surface.list, queryDirty: resourceView.queryDirty });
  const selectedIds = selectable ? surface.selectedIds : EMPTY_SELECTED_IDS;
  // "Select all N" (opt-in) selects what the surface's read behind its pager matches.
  // The mode holds that read's key, so it is only ever this read's, and the total it
  // last settled with, which it keeps while the read pages, refetches or fails.
  const matching = surface.matching;
  // The surface holds the read from its own request, so its key and copy follow that
  // request, not each render (a scroll frame re-renders the list).
  const selects = matching?.selects;
  const matchingKey = React.useMemo(() => selects ? stableSerialize(selects) : null, [selects]);
  // Under the mode every loaded row shows selected, and a write starts from them, with
  // the row and the place each shows in.
  const table = surface.table;
  const groupedItems = surface.kind === "grouped" ? surface.groupedItems : null;
  const placedShown = React.useMemo(() => groupedItems ? placedRecords(groupedItems) : null, [groupedItems]);
  const shown = React.useMemo<ShownSelection | undefined>(() => matching && matchingKey !== null ? {
    readKey: matchingKey, ids: matching.loaded,
    observed: (id) => table.getCoreRowModel().rowsById[id]?.original,
    ...(placedShown ? { placed: (id: string) => placedShown.get(id) ?? undefined } : {}),
  } : undefined, [matching, matchingKey, table, placedShown]);
  const placement = useSelectionPlaces(shown);
  const mode = placement.allMatching;
  const chosen = mode?.readKey === matchingKey ? mode : null;
  const surfaceId = React.useId();
  // Only the list that chose the mode moves its total; another list with the same read
  // shows the mode's, so two lists whose reads settle apart never write over each other.
  const entered = chosen?.surface === surfaceId ? chosen : null;
  const chosenTotal = chosen ? (entered ? matching?.total : undefined) ?? chosen.total : undefined;
  // A copy: a page changing what it received changes nothing in the list's reads.
  const allMatching = React.useMemo(() => selects && chosenTotal !== undefined
    ? structuredClone<AllMatching>({ ...selects, total: chosenTotal }) : undefined, [selects, chosenTotal]);
  const { updateAllMatching, leaveAllMatching } = placement;
  // This list reading otherwise, or no longer showing, ends the mode it chose: it never
  // comes back by itself, and another list sharing the selection never ends it.
  React.useLayoutEffect(() => matchingKey === null ? undefined : () => leaveAllMatching(surfaceId),
    [matchingKey, leaveAllMatching, surfaceId]);
  // A refetch of the list that chose the mode that settles with another total moves the mode with it.
  React.useLayoutEffect(() => {
    if (entered && matching?.total !== undefined) updateAllMatching(entered.readKey, matching.total);
  }, [entered, matching?.total, updateAllMatching]);
  // While the scope holds the mode, this list's or another's sharing the selection, the
  // generic verbs and the Share utility refuse: they act on each selected row.
  const disabledReason = mode
    ? t(mode.unit === "groups" ? "list.selectedOnlyNotAllGroups" : "list.selectedOnlyNotAllRecords", { count: allMatching?.total ?? mode.total })
    : matching ? null : undefined;
  const deselectSucceeded = React.useCallback((ids: readonly string[]) => {
    resourceView.setRowSelection((selection) => {
      const remaining = { ...selection };
      for (const id of ids) delete remaining[id];
      return remaining;
    });
  }, [resourceView.setRowSelection]);
  const bulk = useBulkAction(source ? null : modelMetadata?.resource, deselectSucceeded, disabledReason);
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
  const visibleFields = resolvedChrome.columnChooser ? surface.visibleFields : EMPTY_ARRAY;
  const contributedUtilities = (
    <ResourceViewUtilities
      modelBacked={!source}
      value={{
        resource: modelMetadata?.resource.modelLabel ?? resource,
        filter: effectiveFilter,
        // Under the mode a utility gets none: one that ignores the reason acts on nothing.
        selectedIds: mode ? EMPTY_SELECTED_IDS : selectedIds,
        selectable,
        disabledReason,
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
    order: listOrderChoice(table),
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
  const renderShown = surface.kind === "grouped" ? surface.shown : null;
  // Value-stable, so a bulk action's memo or effect re-runs only when the split changes.
  const selectedGroups = useValueStable(React.useMemo(() => splitsSelection
    ? selectedGroupsOf(selectedIds, effectiveGroupStack,
      groupedItems && renderShown ? { places: placement.places, items: groupedItems, shown: renderShown } : null)
    : undefined, [splitsSelection, selectedIds, placement.places, effectiveGroupStack, groupedItems, renderShown]));
  // Offered once every loaded row is selected and the read, settled, matches more.
  const offersAll = Boolean(matching?.more && !chosen && matching.loaded.length > 0
    && matching.loaded.every((id) => selectedIds.has(id)));
  const barTotal = allMatching?.total ?? (offersAll ? matching?.total : undefined);
  const barMatching = matching && matchingKey !== null && barTotal !== undefined ? {
    total: barTotal, unit: matching.selects.unit, selected: Boolean(allMatching),
    ...(offersAll ? { onSelectAll: () => placement.selectAllMatching({
      readKey: matchingKey, unit: matching.selects.unit, total: barTotal, surface: surfaceId,
    }) } : {}),
  } : undefined;
  // Every loaded row shows selected in the mode; the selection's ids stay the selected ones.
  const shownSelectedIds = React.useMemo(() => allMatching && matching
    ? new Set([...selectedIds, ...matching.loaded]) : selectedIds, [allMatching, matching?.loaded, selectedIds]);
  // The flat body shows and writes the selection through what it is given: in the mode,
  // every loaded row and the header selected, and this surface's writes, which start from them.
  const flatView = React.useMemo<ResourceViewContextValue>(() => allMatching ? {
    ...resourceView, setRowSelection: placement.setRowSelection, toggleSelectedId: placement.toggleSelectedId,
    state: { ...resourceView.state, rowSelection: Object.fromEntries([...shownSelectedIds].map((id) => [id, true])) },
  } : resourceView, [allMatching, resourceView, placement.setRowSelection, placement.toggleSelectedId, shownSelectedIds]);
  // The header shows checked in the mode, so it only unticks: it clears the page's rows, every row it shows.
  const pageIds: readonly string[] = surface.kind === "flat" ? surface.pageIds : EMPTY_ARRAY;
  const clearPageSelection = React.useCallback(() => placement.setRowSelection((current) => Object.fromEntries(
    Object.entries(current).filter(([id]) => !pageIds.includes(id)))), [placement.setRowSelection, pageIds]);
  const flatSelection = (flat: ResourceViewSurface<TRow>) => allMatching ? {
    resourceView: flatView, allPageSelected: true, somePageSelected: false, onPageSelectionChange: clearPageSelection,
  } : {
    resourceView, allPageSelected: flat.allPageSelected, somePageSelected: flat.somePageSelected,
    onPageSelectionChange: flat.setPageSelection,
  };
  const domainBulkActions = renderBulkActions(bulkActions, selectedIds, {
    selectedRows: resourceView.selectedRows, clearSelectedIds: resourceView.clearSelectedIds, allMatching: mode,
  }, { filter: surface.mergedFilter, selectedGroups, ...(allMatching ? { allMatching } : {}) });
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
        bulk,
        matching: barMatching,
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
          cardHref={scopedRowHref}
          onCardClick={onRowClick}
          draggableRow={draggableRow}
          selectedIds={shownSelectedIds}
          onToggleSelected={selectable ? placement.toggleSelectedId : undefined}
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
          selectedIds={shownSelectedIds}
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
          selectedIds={shownSelectedIds}
          {...(allMatching && shown ? { shown } : {})}
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
          selectedIds={shownSelectedIds}
          onToggleSelected={selectable ? placement.toggleSelectedId : undefined}
          interactive={interactive}
          fetching={surface.list.fetching}
          emptyContent={emptyContent}
          rowHref={scopedRowHref}
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
          {...flatSelection(surface)}
          visibleFields={visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          groupStack={effectiveGroupStack}
          interactive={interactive}
          rowHref={scopedRowHref}
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
          {...flatSelection(surface)}
          visibleFields={visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          groupStack={effectiveGroupStack}
          interactive={interactive}
          rowHref={scopedRowHref}
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
