import * as React from "react";
import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
  useAngeeAggregate,
} from "@angee/refine";
import {
  isClientRowModel,
  useModelMetadata,
} from "@angee/metadata";
import type {
  Row,
} from "@angee/metadata";

import { useUiT } from "../i18n";
import type { PagerState } from "../ui/pager";
import { BoardView } from "./BoardView";
import {
  ResourceViewProvider,
  useResourceView,
  useResourceViewMaybe,
  type ResourceViewContextValue,
} from "./resource-view-context";
import {
  RESOURCE_VIEW_KINDS,
  Filter,
  resourceViewGroupsEqual,
  type ResourceViewDefaultGroups,
  type ResourceViewGroup,
  type ResourceViewKind,
} from "./resource-view-model";
import { DeletePreviewDialog } from "./DeletePreviewDialog";
import {
  useClientResourceViewSurface,
  useGroupedResourceViewSurface,
  useResourceViewSurface,
  type ResourceViewSurface,
  type UseResourceViewSurfaceProps,
} from "./resource-view-surface";
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
import type { ListEmptyContent, ListViewProps } from "./resource-view-types";
import {
  activeFilterIdsFor,
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  createLabelForResource,
  customFilterChipsFor,
  mergeFilterFields,
  mergeFilterOptions,
  mergeGroupOptions,
  resolveResourceViewGroup,
  resolveTextFilterField,
  textFilterValue,
  validResourceViewGroupStack,
} from "./resource-view-utils";
import { columnsWithMetadataDefaults } from "./model-metadata-defaults";
import type { ColumnDescriptor } from "./page";
import { useRelationFacets } from "./relation-facet";
import { useScalarFacets } from "./scalar-facet";
import { useBulkDelete } from "./useBulkDelete";
import { useAggregateOperation } from "./resource-operations";
import { useResourceToolbarProps } from "./resource-toolbar-props";

export type { ResourceListSnapshot } from "./resource-view-surface";
export type {
  ColumnAlign,
  ListColumn,
} from "./resource-view-list-body";
export type {
  CardActionContext,
  ListEmptyAction,
  ListEmptyContent,
  ListEmptyState,
  ListViewProps,
} from "./resource-view-types";

const EMPTY_GROUP_STACK = [] as const;

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
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
      view: props.defaultView,
    }),
    [props.defaultView, props.pageSize],
  );
  // A local-scoped list (an embedded related list on a detail panel) owns its own
  // view state instead of inheriting — and fighting over — the surrounding route
  // data view. The default "inherit" keeps the routed-page behaviour unchanged.
  if (scope !== "local" && resourceView) {
    return <ListViewBody {...props} resourceView={resourceView} />;
  }
  return (
    <ResourceViewProvider
      initialState={initialState}
      resource={props.resource}
      scope={scope === "local" ? "local" : "route"}
    >
      <ListViewBound {...props} />
    </ResourceViewProvider>
  );
}

function ListViewBound<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  return <ListViewBody {...props} resourceView={useResourceView()} />;
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
  pageSize,
  defaultGroup,
  defaultGroups,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  toolbarActions,
  cardActions,
  renderCard,
  emptyContent,
  className,
  resourceView,
}: ListViewProps<TRow> & {
  resourceView: ResourceViewContextValue;
}): React.ReactElement {
  const t = useUiT();
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  const modelMetadata = useModelMetadata(resource);
  const resolvedColumns = React.useMemo(
    () => columnsWithMetadataDefaults(columns, modelMetadata),
    [columns, modelMetadata],
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
  const rawActiveDefaultGroup = defaultGroupForView(
    defaultGroup,
    defaultGroups,
    resourceView.state.view,
  );
  const activeDefaultGroup = React.useMemo(
    () =>
      rawActiveDefaultGroup
        ? resolveResourceViewGroup(rawActiveDefaultGroup, modelMetadata)
        : null,
    [modelMetadata, rawActiveDefaultGroup],
  );
  const validDefaultGroupStack = React.useMemo(
    () =>
      activeDefaultGroup
        ? validResourceViewGroupStack([activeDefaultGroup], modelMetadata)
        : EMPTY_GROUP_STACK,
    [activeDefaultGroup, modelMetadata],
  );
  const validCurrentGroupStack = React.useMemo(
    () => validResourceViewGroupStack(resourceView.state.groupStack, modelMetadata),
    [resourceView.state.groupStack, modelMetadata],
  );
  const hasInvalidGroupStack =
    !resourceViewGroupStacksEqual(resourceView.state.groupStack, validCurrentGroupStack);
  const handledDefaultGroupRef = React.useRef<ResourceViewGroup | null>(null);
  const defaultGroupPending =
    activeDefaultGroup !== null
    && resourceView.state.group === null
    && (
      handledDefaultGroupRef.current === null
      || !resourceViewGroupsEqual(handledDefaultGroupRef.current, activeDefaultGroup)
    );
  const effectiveGroupStack = React.useMemo(() => {
    if (validCurrentGroupStack.length > 0) return validCurrentGroupStack;
    if (hasInvalidGroupStack || defaultGroupPending) return validDefaultGroupStack;
    return resourceView.state.groupStack;
  }, [
    resourceView.state.groupStack,
    hasInvalidGroupStack,
    defaultGroupPending,
    validCurrentGroupStack,
    validDefaultGroupStack,
  ]);
  React.useEffect(() => {
    if (!activeDefaultGroup) {
      handledDefaultGroupRef.current = null;
      return;
    }
    if (
      handledDefaultGroupRef.current
      && resourceViewGroupsEqual(handledDefaultGroupRef.current, activeDefaultGroup)
    ) {
      return;
    }
    const previousDefault = handledDefaultGroupRef.current;
    if (
      resourceView.state.group === null
      || (
        previousDefault
        && resourceViewGroupsEqual(resourceView.state.group, previousDefault)
      )
    ) {
      handledDefaultGroupRef.current = activeDefaultGroup;
      resourceView.setGroup(activeDefaultGroup);
    }
  }, [
    activeDefaultGroup,
    resourceView.setGroup,
    resourceView.state.group,
  ]);
  React.useEffect(() => {
    if (!hasInvalidGroupStack) return;
    if (resourceViewGroupStacksEqual(resourceView.state.groupStack, effectiveGroupStack)) {
      return;
    }
    resourceView.setGroupStack(effectiveGroupStack);
  }, [
    resourceView.setGroupStack,
    resourceView.state.groupStack,
    effectiveGroupStack,
    hasInvalidGroupStack,
  ]);

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
    pageSize,
    resourceView,
    modelMetadata,
    groupStack: effectiveGroupStack,
    enabled: !groupedListMode,
    onListStateChange,
  };
  const content = (surface: ResourceViewSurface<TRow>) => (
    <ListViewContent<TRow>
      surface={surface}
      resource={resource}
      resolvedColumns={resolvedColumns}
      modelMetadata={modelMetadata}
      resourceView={resourceView}
      effectiveGroupStack={effectiveGroupStack}
      clientRowModel={clientRowModel}
      groupedListMode={groupedListMode}
      declaredFacets={declaredFacets}
      scalarFacets={scalarFacets}
      explicitGroupOptions={explicitGroupOptions}
      explicitFilterOptions={explicitFilterOptions}
      explicitCustomFilterFields={explicitCustomFilterFields}
      defaultGroup={defaultGroup}
      defaultGroups={defaultGroups}
      order={order}
      onCreate={onCreate}
      createLabel={createLabel}
      onRowClick={onRowClick}
      onListStateChange={onListStateChange}
      rowHref={rowHref}
      toolbarActions={toolbarActions}
      cardActions={cardActions}
      renderCard={renderCard}
      emptyContent={resolvedEmptyContent}
      className={className}
    />
  );
  // A client resource fetches once and pages in the browser; a server resource
  // queries Hasura per page. The two surface hooks call different data hooks, so
  // the choice is a component boundary (never a conditional hook): a metadata
  // flip remounts the matching surface component rather than reordering hooks.
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

function ServerSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useResourceViewSurface(surfaceProps));
}

function GroupedServerSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useGroupedResourceViewSurface(surfaceProps));
}

function ClientSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useClientResourceViewSurface(surfaceProps));
}

interface ListViewContentProps<TRow extends Row> {
  surface: ResourceViewSurface<TRow>;
  resource: string;
  resolvedColumns: readonly ColumnDescriptor<TRow>[];
  modelMetadata: ReturnType<typeof useModelMetadata>;
  resourceView: ResourceViewContextValue;
  effectiveGroupStack: readonly ResourceViewGroup[];
  clientRowModel: boolean;
  groupedListMode: boolean;
  declaredFacets: ReturnType<typeof useRelationFacets>;
  scalarFacets: ReturnType<typeof useScalarFacets>;
  explicitGroupOptions: ListViewProps<TRow>["groupOptions"];
  explicitFilterOptions: ListViewProps<TRow>["filterOptions"];
  explicitCustomFilterFields: ListViewProps<TRow>["customFilterFields"];
  defaultGroup: ListViewProps<TRow>["defaultGroup"];
  defaultGroups: ListViewProps<TRow>["defaultGroups"];
  order: ListViewProps<TRow>["order"];
  onCreate: ListViewProps<TRow>["onCreate"];
  createLabel: ListViewProps<TRow>["createLabel"];
  onRowClick: ListViewProps<TRow>["onRowClick"];
  onListStateChange: ListViewProps<TRow>["onListStateChange"];
  rowHref: ListViewProps<TRow>["rowHref"];
  toolbarActions: ListViewProps<TRow>["toolbarActions"];
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
  effectiveGroupStack,
  clientRowModel,
  groupedListMode,
  declaredFacets,
  scalarFacets,
  explicitGroupOptions,
  explicitFilterOptions,
  explicitCustomFilterFields,
  defaultGroup,
  defaultGroups,
  order,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  toolbarActions,
  cardActions,
  renderCard,
  emptyContent,
  className,
}: ListViewContentProps<TRow>): React.ReactElement {
  const flatMeasures = React.useMemo(
    () => groupMeasuresFromColumns(resolvedColumns),
    [resolvedColumns],
  );
  const toolbarPager = React.useMemo<PagerState>(() => {
    if (!groupedListMode) {
      return {
        total: surface.list.total,
        page: surface.list.page,
        pageSize: surface.list.pageSize,
        hasPrev: surface.list.hasPrev,
        hasNext: surface.list.hasNext,
      };
    }
    // Group-level pager: the surface reports the level-0 group total; Pager
    // derives hasPrev/hasNext from page/total.
    return {
      total: surface.list.total ?? 0,
      page: surface.list.page,
      pageSize: surface.list.pageSize,
    };
  }, [
    groupedListMode,
    surface.list.hasNext,
    surface.list.hasPrev,
    surface.list.page,
    surface.list.pageSize,
    surface.list.total,
  ]);
  const explicitAndFacetGroupOptions = React.useMemo(
    () => mergeGroupOptions(explicitGroupOptions, declaredFacets.groupOptions),
    [declaredFacets.groupOptions, explicitGroupOptions],
  );
  const toolbarGroupOptions = React.useMemo(
    () =>
      mergeGroupOptions(
        explicitAndFacetGroupOptions,
        buildGroupOptions(
          resolvedColumns,
          modelMetadata,
          defaultGroupsForToolbar(defaultGroup, defaultGroups),
        ),
      ),
    [
      defaultGroup,
      defaultGroups,
      explicitAndFacetGroupOptions,
      modelMetadata,
      resolvedColumns,
    ],
  );
  const inferredCustomFilterFields = React.useMemo(
    () => buildFilterFields(resolvedColumns, surface.rows, modelMetadata),
    [modelMetadata, resolvedColumns, surface.rows],
  );
  const inferredFilterOptions = React.useMemo(
    () => buildFilterOptions(resolvedColumns, surface.rows, inferredCustomFilterFields),
    [inferredCustomFilterFields, resolvedColumns, surface.rows],
  );
  const facetFilters = React.useMemo(
    () => mergeFilterOptions(declaredFacets.filters, scalarFacets.filters),
    [declaredFacets.filters, scalarFacets.filters],
  );
  const explicitAndFacetFilters = React.useMemo(
    () => mergeFilterOptions(explicitFilterOptions, facetFilters),
    [explicitFilterOptions, facetFilters],
  );
  const filterOptions = React.useMemo(
    () => mergeFilterOptions(explicitAndFacetFilters, inferredFilterOptions),
    [explicitAndFacetFilters, inferredFilterOptions],
  );
  const facetCustomFilterFields = React.useMemo(
    () => mergeFilterFields(declaredFacets.filterFields, scalarFacets.filterFields),
    [declaredFacets.filterFields, scalarFacets.filterFields],
  );
  const explicitAndFacetCustomFilterFields = React.useMemo(
    () => mergeFilterFields(explicitCustomFilterFields, facetCustomFilterFields),
    [explicitCustomFilterFields, facetCustomFilterFields],
  );
  const customFilterFields = React.useMemo(
    () => mergeFilterFields(explicitAndFacetCustomFilterFields, inferredCustomFilterFields),
    [explicitAndFacetCustomFilterFields, inferredCustomFilterFields],
  );
  const activeFilterIds = activeFilterIdsFor(
    resourceView.state.filter,
    filterOptions,
  );
  // Search the model's real title field (recordRepresentation → e.g. displayName
  // for Person), not the hardcoded "title" that non-title models lack.
  const textFilterField = resolveTextFilterField(modelMetadata);
  const customFilterChips = customFilterChipsFor(
    resourceView.state.filter,
    filterOptions,
    customFilterFields,
    textFilterField,
  );

  const filterText = textFilterValue(resourceView.state.filter, textFilterField);
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
  const toolbar = useResourceToolbarProps({
    actions: toolbarActions,
    pager: toolbarPager,
    view: resourceView.state.view,
    group: effectiveGroupStack[0] ?? null,
    groupStack: effectiveGroupStack,
    groupOptions: toolbarGroupOptions,
    filterOptions,
    customFilterFields,
    customFilterChips,
    favorites: resourceView.savedFavorites,
    activeFilterIds,
    filterText,
    textFilterField,
    createLabel: createLabel ?? createLabelForResource(resource),
    onCreate,
    resourceView,
    pagerSubject: groupedListMode ? "Groups" : undefined,
    pagerTotalUnit: groupedListMode ? "groups" : undefined,
  });

  return (
    <ResourceListFrame
      className={className}
      toolbar={toolbar}
      selection={{
        count: surface.selectedIds.size,
        onClear: resourceView.clearSelectedIds,
        onDelete: bulkDelete.canDelete ? bulkDelete.deleteInitiate : undefined,
        deletePending: bulkDelete.isPending,
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
      {groupedListMode ? (
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
          onRowClick={onRowClick}
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
          selectedIds={surface.selectedIds}
          interactive={interactive}
          fetching={surface.list.fetching}
          emptyContent={emptyContent}
          rowHref={rowHref}
          onRowClick={onRowClick}
          cardActions={cardActions}
          cardActionContext={cardActionContext}
          renderCard={renderCard}
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
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyContent={emptyContent}
          fetching={surface.list.fetching}
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
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyContent={emptyContent}
          fetching={surface.list.fetching}
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

function requireDataResource(
  resourceId: string,
  metadata: ReturnType<typeof useModelMetadata>,
): NonNullable<NonNullable<ReturnType<typeof useModelMetadata>>["resource"]> {
  const dataResource = metadata?.resource;
  if (!dataResource) {
    throw new Error(`Resource "${resourceId}" has no data resource metadata.`);
  }
  return dataResource;
}

function defaultGroupForView(
  defaultGroup: ResourceViewGroup | null | undefined,
  defaultGroups: ResourceViewDefaultGroups | undefined,
  view: ResourceViewKind,
): ResourceViewGroup | null {
  if (
    defaultGroups
    && Object.prototype.hasOwnProperty.call(defaultGroups, view)
  ) {
    return defaultGroups[view] ?? null;
  }
  return defaultGroup ?? null;
}

function defaultGroupsForToolbar(
  defaultGroup: ResourceViewGroup | null | undefined,
  defaultGroups: ResourceViewDefaultGroups | undefined,
): readonly ResourceViewGroup[] {
  const groups: ResourceViewGroup[] = [];
  if (defaultGroup) groups.push(defaultGroup);
  for (const view of RESOURCE_VIEW_KINDS) {
    const group = defaultGroups?.[view];
    if (group) groups.push(group);
  }
  return groups;
}

function resourceViewGroupStacksEqual(
  left: readonly ResourceViewGroup[],
  right: readonly ResourceViewGroup[],
): boolean {
  return (
    left.length === right.length
    && left.every((group, index) => {
      const other = right[index];
      return other !== undefined && resourceViewGroupsEqual(group, other);
    })
  );
}
