import * as React from "react";

import type { DndPayload } from "../lib/dnd";
import { useUiT } from "../i18n";
import { GalleryView } from "./GalleryView";
import {
  ResourceViewSwitcher,
  type ResourceToolbarFilterField,
  type ResourceToolbarFilterOption,
  type ResourceToolbarGroupOption,
} from "../toolbars";
import type { PagerState } from "../ui/pager";
import {
  ResourceViewProvider,
  useResourceView,
  useResourceViewMaybe,
  type ResourceViewContextValue,
} from "./resource-view-context";
import {
  resourceViewGroupsEqual,
  type ResourceViewGroup,
} from "./resource-view-model";
import {
  useRowsResourceViewSurface,
  type ResourceListSnapshot,
  type StringIdRow,
} from "./resource-view-surface";
import {
  FlatListBody,
  type ListColumn,
} from "./resource-view-list-body";
import { ResourceListFrame } from "./ResourceListFrame";
import type { ListEmptyContent } from "./resource-view-types";
import {
  activeFilterIdsFor,
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  customFilterChipsFor,
  mergeFilterFields,
  mergeFilterOptions,
  textFilterValue,
} from "./resource-view-utils";
import { useResourceToolbarProps } from "./resource-toolbar-props";

export interface RowsListViewProps<TRow extends StringIdRow = StringIdRow> {
  rows: readonly TRow[];
  columns: readonly ListColumn<TRow>[];
  filterOptions?: readonly ResourceToolbarFilterOption[];
  customFilterFields?: readonly ResourceToolbarFilterField[];
  groupOptions?: readonly ResourceToolbarGroupOption[];
  defaultGroup?: ResourceViewGroup | null;
  pageSize?: number;
  fetching?: boolean;
  error?: Error | null;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  rowHref?: (row: TRow) => string;
  emptyContent?: ListEmptyContent;
  className?: string;
  selectable?: boolean;
  /** Controls rendered in the toolbar's leading slot, beside the filter. */
  toolbarActions?: React.ReactNode;
  /**
   * Opt into a List/Grid switcher: when set, the toolbar gains a layout toggle
   * and the Grid mode renders each row as a {@link GalleryView} card over the
   * same filtered/paged surface. Navigation reuses `rowHref`/`onRowClick`.
   */
  gallery?: RowsGalleryConfig<TRow>;
  /** Bulk actions rendered in the selection bar when rows are selected. */
  bulkActions?: (
    selectedIds: ReadonlySet<string>,
    clear: () => void,
  ) => React.ReactNode;
  /** Make each row/card draggable by returning its dnd payload, or `null`. */
  draggableRow?: (row: TRow) => DndPayload | null;
  /** Use local resource-view state even when rendered inside another data view. */
  scope?: "inherit" | "local";
}

/** Card presentation for {@link RowsListViewProps.gallery}; mirrors GalleryView. */
export interface RowsGalleryConfig<TRow extends StringIdRow = StringIdRow> {
  image?: keyof TRow & string;
  title?: keyof TRow & string;
  subtitle?: keyof TRow & string;
  renderCard?: (row: TRow) => React.ReactNode;
}

type RowLayout = "list" | "grid";

export function RowsListView<TRow extends StringIdRow = StringIdRow>(
  props: RowsListViewProps<TRow>,
): React.ReactElement {
  const resourceView = useResourceViewMaybe();
  const scope = props.scope ?? "inherit";
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (scope !== "local" && resourceView) {
    return <RowsListViewBody {...props} resourceView={resourceView} />;
  }
  const providerScope = scope === "local" ? "local" : "route";
  return (
    <ResourceViewProvider initialState={initialState} scope={providerScope}>
      <RowsListViewBound {...props} />
    </ResourceViewProvider>
  );
}

function RowsListViewBound<TRow extends StringIdRow = StringIdRow>(
  props: RowsListViewProps<TRow>,
): React.ReactElement {
  return <RowsListViewBody {...props} resourceView={useResourceView()} />;
}

function RowsListViewBody<TRow extends StringIdRow = StringIdRow>({
  rows,
  columns,
  filterOptions: explicitFilterOptions,
  customFilterFields: explicitCustomFilterFields,
  groupOptions,
  defaultGroup,
  pageSize,
  fetching = false,
  error = null,
  onRowClick,
  onListStateChange,
  rowHref,
  emptyContent,
  className,
  selectable = false,
  toolbarActions,
  gallery,
  bulkActions,
  draggableRow,
  resourceView,
}: RowsListViewProps<TRow> & {
  resourceView: ResourceViewContextValue;
}): React.ReactElement {
  const t = useUiT();
  const [layout, setLayout] = React.useState<RowLayout>("list");
  const handledDefaultGroupRef = React.useRef<ResourceViewGroup | null>(null);
  React.useEffect(() => {
    if (!defaultGroup) {
      handledDefaultGroupRef.current = null;
      return;
    }
    if (
      handledDefaultGroupRef.current
      && resourceViewGroupsEqual(handledDefaultGroupRef.current, defaultGroup)
    ) {
      return;
    }
    handledDefaultGroupRef.current = defaultGroup;
    if (resourceView.state.group === null) resourceView.setGroup(defaultGroup);
  }, [resourceView.setGroup, resourceView.state.group, defaultGroup]);

  const surface = useRowsResourceViewSurface({
    rows,
    columns,
    pageSize,
    resourceView,
    fetching,
    error,
    onListStateChange,
  });
  const toolbarPager = React.useMemo<PagerState>(
    () => ({
      total: surface.list.total,
      page: surface.list.page,
      pageSize: surface.list.pageSize,
      hasPrev: surface.list.hasPrev,
      hasNext: surface.list.hasNext,
    }),
    [
      surface.list.hasNext,
      surface.list.hasPrev,
      surface.list.page,
      surface.list.pageSize,
      surface.list.total,
    ],
  );
  const toolbarGroupOptions = React.useMemo(
    () => groupOptions ?? buildGroupOptions(columns, null, defaultGroup),
    [columns, defaultGroup, groupOptions],
  );
  const groupingEnabled =
    toolbarGroupOptions.length > 0 || resourceView.state.groupStack.length > 0;
  const inferredCustomFilterFields = React.useMemo(
    () => buildFilterFields(columns, surface.sourceRows, null),
    [columns, surface.sourceRows],
  );
  const customFilterFields = React.useMemo(
    () => mergeFilterFields(explicitCustomFilterFields, inferredCustomFilterFields),
    [explicitCustomFilterFields, inferredCustomFilterFields],
  );
  const inferredFilterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.sourceRows, inferredCustomFilterFields),
    [columns, inferredCustomFilterFields, surface.sourceRows],
  );
  const filterOptions = React.useMemo(
    () => mergeFilterOptions(explicitFilterOptions, inferredFilterOptions),
    [explicitFilterOptions, inferredFilterOptions],
  );
  const activeFilterIds = activeFilterIdsFor(
    resourceView.state.filter,
    filterOptions,
  );
  const customFilterChips = customFilterChipsFor(
    resourceView.state.filter,
    filterOptions,
    customFilterFields,
  );
  const filterText = textFilterValue(resourceView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  const toolbar = useResourceToolbarProps({
    actions: toolbarActions,
    viewSwitcher: gallery ? (
      <ResourceViewSwitcher<RowLayout>
        mode="layout"
        view={layout}
        onViewChange={setLayout}
      />
    ) : undefined,
    pager: toolbarPager,
    group: resourceView.state.group,
    groupStack: resourceView.state.groupStack,
    groupOptions: toolbarGroupOptions,
    groupingEnabled,
    filterOptions,
    customFilterFields,
    customFilterChips,
    favorites: resourceView.savedFavorites,
    activeFilterIds,
    filterText,
    resourceView,
  });

  return (
    <ResourceListFrame
      className={className}
      toolbar={toolbar}
      selection={
        selectable
          ? {
              count: surface.selectedIds.size,
              onClear: resourceView.clearSelectedIds,
              actions: surface.selectedIds.size > 0
                ? bulkActions?.(surface.selectedIds, resourceView.clearSelectedIds)
                : undefined,
            }
          : undefined
      }
      error={error}
      loadingFooter={fetching && surface.rowModels.length > 0}
    >
      {gallery && layout === "grid" ? (
        <GalleryView<TRow>
          rows={surface.rowModels.map((model) => model.original)}
          imageField={gallery.image}
          titleField={gallery.title}
          subtitleField={gallery.subtitle}
          renderCard={gallery.renderCard}
          cardHref={rowHref}
          onCardClick={onRowClick}
          draggableRow={draggableRow}
          selectedIds={selectable ? surface.selectedIds : undefined}
          onToggleSelected={selectable ? resourceView.toggleSelectedId : undefined}
          fetching={fetching}
          emptyContent={resolvedEmptyContent}
        />
      ) : (
        <FlatListBody
          columns={columns}
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
          selectable={selectable}
          rowHref={rowHref}
          onRowClick={onRowClick}
          draggableRow={draggableRow}
          emptyContent={resolvedEmptyContent}
          fetching={fetching}
        />
      )}
    </ResourceListFrame>
  );
}
