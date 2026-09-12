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
import { withResourceViewScope, useResourceViewMaybe, type ResourceViewContextValue } from "../resource-view-context";
import { Filter, availableResourceViewKinds } from "../resource-view-model";
import { CalendarCollectionSurface } from "../../calendar/calendar-collection-surface";
import { type GroupedResourceViewSurface, type ResourceViewSurface, type UseResourceViewSurfaceProps } from "../resource-view-surface";
import type { ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import type { ListViewProps } from "../resource-view-types";
import { resolveResourceViewGroup } from "../resource-view-utils";
import { columnsWithMetadataDefaults, relationFieldInfo } from "../model-metadata-defaults";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { defaultGroupForView } from "../resource-view-toolbar-inputs";
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
    scope: props.scope,
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
  if (!error && query) {
    try {
      error = validateResourceViewState(
        props.resourceView.state,
        query,
      ).queryError;
      query.toWhere(props.baseFilter, props.resourceView.state.filter);
      const group =
        props.resourceView.state.view === "board" && props.laneSource
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
        props.resourceView.state.view === "board" && props.laneSource && group
          ? [group]
          : groups.length > 0
            ? groups
            : group
              ? [group]
              : [];
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
  return error ? (
    <ResourceQueryError error={error} onReset={props.resourceView.resetQuery} />
  ) : (
    <ListViewBody {...props} />
  );
}

function ListViewBody<TRow extends Row = Row>({
  resource,
  source,
  availableViews: declaredViews,
  textFilterField,
  maxGroupDepth,
  toolbarWrap,
  renderGroupLabel,
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
  defaultExpandedGroups,
  calendar,
  laneSource: laneSourceInput,
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
  // The Calendar kind is offered only where the page declares occurrence sources;
  // the switcher's options derive from that (list + board always).
  const calendarAvailable = (calendar?.sources.length ?? 0) > 0;
  const dashboardAvailable = !source && Boolean(modelMetadata?.resource?.roots.aggregate);
  const availableViews = React.useMemo(
    () =>
      declaredViews ??
      availableResourceViewKinds({ calendar: calendarAvailable, dashboard: dashboardAvailable }),
    [declaredViews, calendarAvailable, dashboardAvailable],
  );
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
    () => Filter.combineOptional(baseFilter, resourceView.state.filter),
    [resourceView.state.filter, baseFilter],
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
    resourceView.state.view === "board" && laneSourceGroup !== null;
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
    fields,
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
      maxGroupDepth={maxGroupDepth}
      toolbarWrap={toolbarWrap}
      renderGroupLabel={renderGroupLabel}
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
      renderCard={renderCard}
      emptyContent={resolvedEmptyContent}
      className={className}
      presentation={presentation}
    />
  );
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
