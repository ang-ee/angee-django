import * as React from "react";
import { stableSerialize } from "@angee/refine";
import { isClientRowModel, modelMetadataForLabel, useModelMetadata, useSchemaFieldMetadata } from "@angee/metadata";
import type { Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { useValueStable } from "../../../lib/use-value-stable";
import { withResourceViewScope, useResourceViewMaybe, type ResourceViewContextValue } from "../resource-view-context";
import { Filter, availableResourceViewKinds } from "../resource-view-model";
import { CalendarCollectionSurface } from "../../calendar/calendar-collection-surface";
import { type GroupedResourceViewSurface, type ResourceViewSurface, type UseResourceViewSurfaceProps } from "../resource-view-surface";
import type { ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import { resourceViewGroupToAggregateDimension } from "../resource-view-list-body";
import type { ListViewProps } from "../resource-view-types";
import { resolveResourceViewGroup } from "../resource-view-utils";
import { columnsWithMetadataDefaults, relationFieldInfo } from "../model-metadata-defaults";
import { useRelationFacets } from "../../relation/relation-facet";
import { useScalarFacets } from "../../relation/scalar-facet";
import { defaultGroupForView } from "../resource-view-toolbar-inputs";
import { useResourceViewGroupState } from "../resource-view-group-state";
import { useRowActionsSurface } from "../RowActions";
import { isBoardFoldField, isBoardRankField, ListViewContent } from "./content";
import { ClientSurfaceBody, GroupedServerSurfaceBody, ServerSurfaceBody } from "./surface-adapters";
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
    const rankFieldMetadata = laneSource.rankField
      ? modelMetadata?.fields[laneSource.rankField]
      : undefined;
    if (
      laneSource.rankField
      && modelMetadata
      && !isBoardRankField(rankFieldMetadata)
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
      laneSource.foldField
      && laneModelMetadata
      && !isBoardFoldField(foldFieldMetadata)
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
