import * as React from "react";
import {
  ResourceQuery,
  isClientRowModel,
  modelMetadataForLabel,
  useModelMetadata,
  useSchemaFieldMetadata,
} from "@angee/metadata";
import type { Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { useValueStable } from "../../../lib/use-value-stable";
import { modelChain, useAppRuntime, useContainer, useResourceRecordMatchFields, type ResourceViewKindContent } from "../../../runtime";
import { withResourceViewScope, useResourceViewMaybe, type ResourceViewContextValue } from "../resource-view-context";
import { DEFAULT_TEXT_FILTER_FIELD, Filter, isBuiltInResourceViewKind } from "../resource-view-model";
import { ResourceViewKindsProvider, useOfferedResourceViewKinds, useResourceViewKindContent } from "../resource-view-kinds";
import { ContributedViewSurface } from "./contributed-view-surface";
import { GanttCollectionSurface } from "../../gantt/gantt-collection-surface";
import { LinkedGanttCollectionSurface } from "../../gantt/linked-gantt-collection-surface";
import { CalendarCollectionSurface } from "../../calendar/calendar-collection-surface";
import { type GroupedResourceViewSurface, type ResourceViewSurface, type UseResourceViewSurfaceProps } from "../resource-view-surface";
import type { ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import type { ListViewProps } from "../resource-view-types";
import { resolveResourceViewGroup } from "../resource-view-utils";
import { columnsWithMetadataDefaults, relationFieldInfo } from "../model-metadata-defaults";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { defaultGroupForView } from "../search/group-defaults";
import { useResourceViewGroupState } from "../resource-view-group-state";
import { initialResourceSorting } from "../resource-view-codecs";
import { useRowActionsSurface } from "../RowActions";
import { isBoardFoldField, isBoardRankField, ListViewContent } from "./content";
import { ClientSurfaceBody, GroupedServerSurfaceBody, ServerSurfaceBody } from "./surface-adapters";
import { ResourceQueryError } from "../ResourceQueryError";
import { validateResourceViewState } from "../model/state";
import { ErrorBanner } from "../../../fragments/ErrorBanner";
import { DashboardCollectionSurface } from "../../../dashboard/surface";
export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  if (props.boardCard && (!props.boardCard.title || (props.boardCard.fields?.length ?? 0) > 4)) {
    throw new Error("ListView boardCard needs a title and at most four detail fields.");
  }
  return <ListViewFrame {...props} />;
}

function ListViewFrame<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  const resourceView = useResourceViewMaybe();
  const metadata = useModelMetadata(props.source ? "" : props.resource);
  const modelMetadata = props.source ? null : metadata;
  const initial = React.useMemo(() => {
    try {
      return {
        state: {
          pageSize: props.pageSize,
          view: props.defaultView,
          sorting: initialResourceSorting(modelMetadata, props.order),
        },
        error: null,
      };
    } catch (error) {
      return {
        state: {},
        error:
          error instanceof Error ? error : new Error("Invalid declared query."),
      };
    }
  }, [props.defaultView, props.pageSize, props.order, modelMetadata]);
  if (initial.error) return <ErrorBanner description={initial.error.message} />;
  return withResourceViewScope({
    ambient: resourceView,
    resource: props.source ? undefined : props.resource,
    baseFilter: props.baseFilter,
    presetIds: props.presetIds,
    scope: props.scope,
    presentation: props.presentation,
    initialState: initial.state,
    children: (scopedResourceView) => (
      <ValidatedListViewBody {...props} resourceView={scopedResourceView} />
    ),
  });
}

function ValidatedListViewBody<TRow extends Row>(
  props: ListViewProps<TRow> & { resourceView: ResourceViewContextValue },
): React.ReactElement {
  const metadata = useModelMetadata(props.source ? "" : props.resource);
  const query =
    props.source?.query ?? (metadata ? ResourceQuery.from(metadata) : null);
  let error = props.resourceView.state.queryError;
  if (!error && props.renderItem && props.resourceView.state.view !== "list") {
    error = new Error("Ordered item lists support only the flat list view.");
  }
  if (!error && query) {
    try {
      error = validateResourceViewState(
        props.resourceView.state,
        query,
      ).queryError;
      query.toWhere(props.resourceView.baseFilter, props.resourceView.state.filter);
      const group =
        (props.resourceView.state.view === "board" || props.resourceView.state.view === "gantt") && props.laneSource
          ? { field: props.laneSource.field }
          : defaultGroupForView(
              props.defaultGroup,
              props.defaultGroups,
              props.resourceView.state.view,
            );
      if (group) query.group(group);
      const groups = query.groupsFrom(props.resourceView.state.groupStack);
      if (
        props.maxGroupDepth !== undefined &&
        groups.length > props.maxGroupDepth
      ) {
        throw new Error(
          `This collection supports at most ${props.maxGroupDepth} grouping levels.`,
        );
      }
      const effectiveGroups =
        (props.resourceView.state.view === "board" || props.resourceView.state.view === "gantt") && props.laneSource && group
          ? [group]
          : groups.length > 0
            ? groups
            : group
              ? [group]
              : [];
      if (props.renderItem && effectiveGroups.length > 0) {
        throw new Error("Ordered item lists do not support grouping.");
      }
      if (effectiveGroups.length > 0) {
        if (
          !isClientRowModel(metadata?.resource) &&
          (props.resourceView.state.view === "list" ||
            (props.resourceView.state.view === "board" && !props.laneSource))
        ) {
          query.toGroupBy(effectiveGroups);
        } else {
          query.selection(effectiveGroups);
        }
      }
    } catch (cause) {
      error =
        cause instanceof Error ? cause : new Error("Invalid resource query.");
    }
  }
  return (
    <ResourceViewKinds resource={props.source ? "" : props.resource}>
      {error ? (
        <ResourceQueryError error={error} onReset={() => {
          if (props.renderItem) props.resourceView.setView("list");
          props.resourceView.resetQuery();
        }} />
      ) : (
        <ListViewBody {...props} />
      )}
    </ResourceViewKinds>
  );
}

/** The collection's `#views` children (its model and MTI parent), for the switcher, toolbar and body. */
function ResourceViewKinds({ resource, children }: { resource: string; children: React.ReactNode }): React.ReactElement {
  const metadata = useModelMetadata(resource);
  const models = useResourceModels(resource, metadata?.resource?.canonicalLabel);
  const kinds = useContainer<ResourceViewKindContent>("resource#views", { models });
  const composed = Boolean(useAppRuntime().containers?.declared["resource#views"]);
  const contents = React.useMemo(() => new Map(kinds.map((kind) => [kind.id, kind.content])), [kinds]);
  // Outside a composed app (a story, an embed) the built-in kinds stand.
  if (!composed) return <>{children}</>;
  return <ResourceViewKindsProvider value={contents}>{children}</ResourceViewKindsProvider>;
}

function useResourceModels(resource: string, canonical: string | null | undefined): readonly string[] {
  return React.useMemo(() => modelChain(canonical, resource), [canonical, resource]);
}

function ListViewBody<TRow extends Row = Row>({
  resource,
  source,
  availableViews: declaredViews,
  textFilterField: declaredTextField,
  maxGroupDepth,
  toolbarWrap,
  tableLayout,
  headerVisibility,
  selectable,
  renderGroupLabel,
  renderItem,
  columns,
  fields,
  baseFilter,
  filterOptions: explicitFilterOptions,
  search: searchDeclaration,
  facets,
  customFilterFields: explicitCustomFilterFields,
  groupOptions: explicitGroupOptions,
  order,
  defaultGroup,
  defaultGroups,
  defaultExpandedGroups,
  calendar,
  gantt,
  laneSource: laneSourceInput,
  boardCard,
  onCreate,
  onCreateInLane,
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
  presentation = "page",
  chrome,
  resourceView,
}: ListViewProps<TRow> & {
  resourceView: ResourceViewContextValue;
}): React.ReactElement {
  const t = useUiT();
  // A board page declares `laneSource` inline (`{ field, filters: fn(id), … }`),
  // so it arrives as a fresh identity every render. That identity cascades
  // through `resolvedLaneSource` → the pinned board group → the group-state
  // effect, which re-dispatches `setGroup` faster than the async URL write can
  // settle `state.group` — an update-depth loop. Collapsing value-equal
  // laneSource back to one identity lets the derived group memoise and the
  // effect settle after a single dispatch.
  const laneSource = useValueStable(laneSourceInput);
  const rowActionSurface = useRowActionsSurface(rowActions);
  const filtered = Object.keys(resourceView.state.filter).length > 0;
  const resolvedEmptyContent = filtered
    ? {
        title: t("list.noMatchingRecords"),
        description: t("list.noMatchingRecordsHint"),
        action: {
          label: t("resourceToolbar.clearQuery"),
          onClick: resourceView.resetQuery,
        },
      }
    : emptyContent ?? t("list.empty");
  const discoveredMetadata = useModelMetadata(source ? "" : resource);
  const modelMetadata = source ? null : discoveredMetadata;
  const textFilterField = declaredTextField === undefined && !modelMetadata ? DEFAULT_TEXT_FILTER_FIELD : declaredTextField;
  const recordMatchFields = useResourceRecordMatchFields(source ? "" : resource);
  const queryFields = useValueStable([...(fields ?? []), ...recordMatchFields, ...(boardCard ? [boardCard.title, ...(boardCard.fields ?? [])] : [])]);
  // The Calendar kind is offered only where the page declares occurrence sources;
  // the switcher's options derive from that (list + board always).
  const ganttAvailable = !source && Boolean(gantt && (laneSource || gantt.linked) && modelMetadata);
  const calendarAvailable = (calendar?.sources.length ?? 0) > 0;
  const dashboardAvailable = !source && Boolean(modelMetadata?.resource?.roots.aggregate);
  const declaredSources = React.useMemo(
    () => ({ calendar: calendarAvailable, gantt: ganttAvailable, dashboard: dashboardAvailable }),
    [calendarAvailable, ganttAvailable, dashboardAvailable],
  );
  const offered = useOfferedResourceViewKinds(declaredSources);
  // A page's own list of views is narrowed by what the layers offer; otherwise the container decides.
  const availableViews = React.useMemo(
    () => declaredViews ? declaredViews.filter((kind) => offered.includes(kind)) : offered,
    [declaredViews, offered],
  );
  const contributedKind = useResourceViewKindContent(resourceView.state.view);
  const schemaMetadata = useSchemaFieldMetadata();
  const resolvedLaneSource =
    React.useMemo<ResolvedBoardLaneSource | null>(() => {
      if (!laneSource) return null;
      const fieldMetadata = modelMetadata?.fields[laneSource.field];
      const relation = relationFieldInfo(
        laneSource.field,
        modelMetadata,
        schemaMetadata,
      );
      if (!relation) {
        if (modelMetadata) {
          throw new Error(
            `ListView laneSource field "${laneSource.field}" must resolve to a relation.`,
          );
        }
        return null;
      }
      if (!fieldMetadata) return null;
      const rankFieldMetadata = laneSource.rankField
        ? modelMetadata?.fields[laneSource.rankField]
        : undefined;
      if (
        laneSource.rankField &&
        modelMetadata &&
        !isBoardRankField(rankFieldMetadata)
      ) {
        throw new Error(
          `ListView laneSource rankField "${laneSource.rankField}" must resolve to a non-null Float field.`,
        );
      }
      const laneModelMetadata = modelMetadataForLabel(
        schemaMetadata,
        relation.resource,
      );
      const foldFieldMetadata = laneSource.foldField
        ? laneModelMetadata?.fields[laneSource.foldField]
        : undefined;
      if (
        laneSource.foldField &&
        laneModelMetadata &&
        !isBoardFoldField(foldFieldMetadata)
      ) {
        throw new Error(
          `ListView laneSource foldField "${laneSource.foldField}" must resolve to a Boolean field on "${relation.resource}".`,
        );
      }
      return {
        ...laneSource,
        relation,
        fieldMetadata,
        ...(rankFieldMetadata ? { rankFieldMetadata } : {}),
        ...(foldFieldMetadata ? { foldFieldMetadata } : {}),
      };
    }, [laneSource, modelMetadata, schemaMetadata]);
  const resolvedColumns = React.useMemo(
    () => columnsWithMetadataDefaults(columns, modelMetadata, schemaMetadata),
    [columns, modelMetadata, schemaMetadata],
  );
  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(resourceView.baseFilter, resourceView.state.filter),
    [resourceView.state.filter, resourceView.baseFilter],
  );
  const declaredFacets = useRelationFacets(
    source ? "" : resource,
    facets,
    mergedFilter,
  );
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
    (resourceView.state.view === "board" || resourceView.state.view === "gantt") && laneSourceGroup !== null;
  const rawActiveDefaultGroup = boardGroupingPinned
    ? laneSourceGroup
    : defaultGroupForView(defaultGroup, defaultGroups, resourceView.state.view);
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
  const serverGroupedMode =
    (resourceView.state.view === "list" ||
      (resourceView.state.view === "board" && !resolvedLaneSource)) &&
    effectiveGroupStack.length > 0 &&
    !clientRowModel;
  const surfaceProps: UseResourceViewSurfaceProps<TRow> = {
    resource,
    source,
    columns: resolvedColumns,
    fields: queryFields,
    filter: baseFilter,
    order,
    resourceView,
    modelMetadata,
    groupStack: effectiveGroupStack,
    defaultExpandedGroups,
    laneSource: resolvedLaneSource,
    enabled: !serverGroupedMode,
    onListStateChange,
  };
  const content = (
    surface: ResourceViewSurface<TRow> | GroupedResourceViewSurface<TRow>,
  ) => (
    <ListViewContent<TRow>
      surface={surface}
      resource={resource}
      source={source}
      textFilterField={textFilterField}
      maxGroupDepth={renderItem ? 0 : maxGroupDepth}
      toolbarWrap={toolbarWrap}
      tableLayout={tableLayout}
      headerVisibility={headerVisibility}
      selectable={renderItem ? false : selectable}
      renderGroupLabel={renderGroupLabel}
      renderItem={renderItem}
      resolvedColumns={resolvedColumns}
      modelMetadata={modelMetadata}
      resourceView={resourceView}
      availableViews={availableViews}
      effectiveGroupStack={effectiveGroupStack}
      effectiveFilter={mergedFilter}
      boardGroupingPinned={boardGroupingPinned}
      clientRowModel={clientRowModel}
      serverGroupedMode={serverGroupedMode}
      declaredFacets={declaredFacets}
      scalarFacets={scalarFacets}
      explicitGroupOptions={explicitGroupOptions}
      explicitFilterOptions={explicitFilterOptions}
      searchDeclaration={searchDeclaration}
      explicitCustomFilterFields={explicitCustomFilterFields}
      defaultGroup={defaultGroup}
      defaultGroups={defaultGroups}
      onCreate={onCreate}
      onCreateInLane={onCreateInLane}
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
      boardCard={boardCard}
      renderCard={renderCard}
      emptyContent={resolvedEmptyContent}
      className={className}
      presentation={presentation}
      chrome={chrome}
    />
  );
  const searchInput = {
    columns: resolvedColumns, modelMetadata, resourceView, search: searchDeclaration, renderItem: Boolean(renderItem),
    query: source?.query, inferOptions: !source,
    defaultGroup, defaultGroups, textFilterField, groupOptions: explicitGroupOptions,
    declaredFacets, scalarFacets, filterOptions: explicitFilterOptions,
    customFilterFields: explicitCustomFilterFields,
  };
  if (resourceView.state.view === "gantt") {
    if (!ganttAvailable || !gantt || (!resolvedLaneSource && !gantt.linked)) {
      return <ErrorBanner description={t("gantt.requiresSource")} />;
    }
    if (gantt.linked) return <LinkedGanttCollectionSurface
      surfaceProps={surfaceProps} gantt={gantt} availableViews={availableViews}
      presentation={presentation} className={className} onCreate={onCreate} createLabel={createLabel}
      onRowClick={onRowClick} rowHref={rowHref} toolbarActions={toolbarActions} toolbarWrap={toolbarWrap}
      maxGroupDepth={maxGroupDepth} selectable={selectable}
      searchInput={searchInput}
    />;
    return (
      <GanttCollectionSurface
        surfaceProps={surfaceProps}
        gantt={gantt}
        groupingPinned={boardGroupingPinned}
        laneSource={resolvedLaneSource!}
        availableViews={availableViews}
        presentation={presentation}
        className={className}
        onCreate={onCreate}
        createLabel={createLabel}
        onRowClick={onRowClick}
        rowHref={rowHref}
        toolbarActions={toolbarActions}
        toolbarWrap={toolbarWrap}
        maxGroupDepth={maxGroupDepth}
        searchInput={searchInput}
      />
    );
  }
  if (!isBuiltInResourceViewKind(resourceView.state.view)) {
    const Body = availableViews.includes(resourceView.state.view) ? contributedKind?.render : undefined;
    if (!Body) return <ErrorBanner description={t("list.unknownView", { view: resourceView.state.view })} />;
    return (
      <ContributedViewSurface
        searchInput={searchInput}
        resource={resource}
        resourceView={resourceView}
        body={Body}
        availableViews={availableViews}
        createLabel={createLabel}
        onCreate={onCreate}
        toolbarActions={toolbarActions}
        className={className}
      />
    );
  }
  if (resourceView.state.view === "dashboard") {
    if (!dashboardAvailable) {
      return <ErrorBanner description="This resource does not expose the aggregate operations required by dashboards." />;
    }
    return (
      <DashboardCollectionSurface
        resource={resource}
        filter={mergedFilter}
        className={className}
        onReturnToList={() => resourceView.setView("list")}
      />
    );
  }
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
    return (
      <ClientSurfaceBody<TRow> surfaceProps={surfaceProps}>
        {content}
      </ClientSurfaceBody>
    );
  }
  if (serverGroupedMode) {
    return (
      <GroupedServerSurfaceBody<TRow> surfaceProps={surfaceProps}>
        {content}
      </GroupedServerSurfaceBody>
    );
  }
  return (
    <ServerSurfaceBody<TRow> surfaceProps={surfaceProps}>
      {content}
    </ServerSurfaceBody>
  );
}
