import * as React from "react";
import { type ResourceQuery } from "@angee/metadata";

import type { DndPayload } from "../../lib/dnd";
import { useUiT } from "../../i18n";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { GalleryView, useGalleryRows } from "../GalleryView";
import {
  type FilterClauseField,
  type ResourceToolbarFilterOption,
  type ResourceToolbarGroupOption,
} from "../../toolbars";
import {
  withResourceViewScope,
  useResourceViewMaybe,
  type ResourceViewContextValue,
} from "./resource-view-context";
import { DEFAULT_TEXT_FILTER_FIELD, resourceViewKindCapabilities, type ResourceViewGroup, type ResourceViewKind } from "./resource-view-model";
import { EMPTY_SELECTED_IDS } from "./resource-view-codecs";
import { selectedGroupsOf } from "./resource-view-grouped-model";
import { validateResourceViewState } from "./model/state";
import { filterForTextSearch, queryForColumns } from "./resource-query";
import { ResourceQueryError } from "./ResourceQueryError";
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
import { columnsWithMetadataDefaults } from "./model-metadata-defaults";
import {
  listChromeState,
  type CardRenderer,
  type ListSearchDeclaration,
  type ListEmptyContent,
  type ResourceCollectionPresentation,
  type ResourceTableHeaderVisibility,
  type ResourceTableLayout,
  type ListViewProps,
  type PlacedBulkCollection,
} from "./resource-view-types";
import { useResourceSearch } from "./search/use-resource-search";
import { useSearchCatalog } from "./search/catalog";
import type { ResourceToolbarProps } from "../../toolbars";
import { declaredGroupDefaults, useAppliedRepeated, validateDeclaredGroupDefaults, withoutRepeated } from "./search/group-defaults";
import {
  useRowActionsSurface,
  type RowActionDeclaration,
} from "./RowActions";

export interface RowsListViewProps<TRow extends StringIdRow = StringIdRow> {
  rows: readonly TRow[];
  /** Explicit local query fields, including relations and fields outside display columns. */
  query?: ResourceQuery;
  columns: readonly ListColumn<TRow>[];
  /** Combined box and optional controls over this collection's search model. */
  search?: ListSearchDeclaration;
  filterOptions?: readonly ResourceToolbarFilterOption[];
  customFilterFields?: readonly FilterClauseField[];
  groupOptions?: readonly ResourceToolbarGroupOption[];
  defaultGroup?: ResourceViewGroup | readonly ResourceViewGroup[] | null;
  pageSize?: number;
  fetching?: boolean;
  error?: Error | null;
  onRowClick?: (row: TRow) => void;
  activeRowId?: string | null;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  rowHref?: (row: TRow) => string;
  /** Authored and page-owned verbs rendered in the shared trailing action column. */
  rowActions?: readonly RowActionDeclaration<TRow>[];
  /** Move a local row to another row's position; the list owns handles and drop targets. */
  onReorder?: (fromId: string, toId: string) => void;
  /** Whether a row may move; a row that may not shows a lock where its handle would be. */
  canReorderRow?: (row: TRow) => boolean;
  /** Content of a spanning table footer row, for example an add-record control. */
  footerRow?: React.ReactNode;
  emptyContent?: ListEmptyContent;
  className?: string;
  presentation?: ResourceCollectionPresentation;
  tableLayout?: ResourceTableLayout;
  headerVisibility?: ResourceTableHeaderVisibility;
  selectable?: ListViewProps<TRow>["selectable"];
  /** Controls rendered in the toolbar's leading slot, beside the filter. */
  toolbarActions?: React.ReactNode;
  /**
   * Offer the `gallery` view beside the list: the switcher toggles the view
   * state (`?view=gallery` where the collection owns the URL), and the gallery
   * renders each row as a {@link GalleryView} card over the same filtered, paged
   * surface. Navigation reuses `rowHref`/`onRowClick`.
   */
  gallery?: RowsGalleryConfig<TRow>;
  /** Bulk actions rendered in the selection bar when rows are selected. */
  bulkActions?: ListViewProps<TRow>["bulkActions"];
  /** Make each row/card draggable by returning its dnd payload, or `null`. */
  draggableRow?: (row: TRow) => DndPayload | null;
  /** Override collection-state ownership. Embedded collections default to local;
   * page/workspace collections default to inherited/route state. */
  scope?: "inherit" | "local";
}

/** The kinds an in-memory list with a gallery offers. */
const ROWS_GALLERY_KINDS: readonly ResourceViewKind[] = ["list", "gallery"];
const NO_GROUPS: readonly ResourceViewGroup[] = [];

/** Card presentation for {@link RowsListViewProps.gallery}; mirrors GalleryView. */
export interface RowsGalleryConfig<TRow extends StringIdRow = StringIdRow> {
  image?: keyof TRow & string;
  title?: keyof TRow & string;
  subtitle?: keyof TRow & string;
  renderCard?: CardRenderer<TRow>;
}

/**
 * @kind surface
 */
export function RowsListView<TRow extends StringIdRow = StringIdRow>(
  props: RowsListViewProps<TRow>,
): React.ReactElement {
  const resourceView = useResourceViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
      ...declaredGroupDefaults(props.defaultGroup),
    }),
    [props.pageSize, props.defaultGroup],
  );
  return withResourceViewScope({
    ambient: resourceView,
    scope: props.scope,
    presentation: props.presentation,
    initialState,
    children: (scopedResourceView) => (
      <ValidatedRowsListView {...props} resourceView={scopedResourceView} />
    ),
  });
}

function ValidatedRowsListView<TRow extends StringIdRow>(
  props: RowsListViewProps<TRow> & { resourceView: ResourceViewContextValue },
): React.ReactElement {
  const t = useUiT();
  const defaults = React.useMemo(() => declaredGroupDefaults(props.defaultGroup), [props.defaultGroup]);
  const query = React.useMemo(
    () => props.query ?? queryForColumns(props.columns, null, defaults.groups),
    [props.query, props.columns, defaults],
  );
  // Local rows group in the browser, which cannot apply `GroupSpec.repeated`; the
  // gallery never groups, so it leaves a stored flag alone, as ListView's gallery does.
  const { grouping } = resourceViewKindCapabilities(props.resourceView.state.view);
  const resourceView = useAppliedRepeated(props.resourceView, !grouping);
  let state = resourceView.state;
  try {
    validateDeclaredGroupDefaults("RowsListView", props.defaultGroup, undefined, resourceView.defaultState, t);
  } catch (error) {
    return <ErrorBanner description={error instanceof Error ? error.message : String(error)} />;
  }
  try {
    query.groupsFrom(defaults.groupStack);
    state = validateResourceViewState({
      ...state,
      filter: filterForTextSearch(query, state.filter, "title", props.columns.map(({ field }) => field)),
      groupStack: grouping ? state.groupStack : withoutRepeated(state.groupStack),
    }, query, { appliesRepeated: false });
  } catch (error) {
    state = { ...state, queryError: error instanceof Error ? error : new Error("Invalid query.") };
  }
  return state.queryError
    ? <ResourceQueryError error={state.queryError} onReset={resourceView.resetQuery} />
    : <RowsListViewBody {...props} resourceView={resourceView} />;
}

function RowsListViewBody<TRow extends StringIdRow = StringIdRow>({
  rows,
  query,
  columns: declaredColumns,
  search: searchDeclaration,
  filterOptions: explicitFilterOptions,
  customFilterFields: explicitCustomFilterFields,
  groupOptions,
  defaultGroup,
  fetching = false,
  error = null,
  onRowClick,
  activeRowId,
  onListStateChange,
  rowHref,
  rowActions,
  onReorder,
  canReorderRow,
  footerRow,
  emptyContent,
  className,
  presentation = "page",
  tableLayout = "auto",
  headerVisibility = "visible",
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
  // Local rows carry no model metadata; the column defaults still resolve a declared
  // `subline` into its cell column, as the list frame does for a resource.
  const columns = React.useMemo(() => columnsWithMetadataDefaults(declaredColumns, null), [declaredColumns]);
  const rowActionSurface = useRowActionsSurface(rowActions);
  // The gallery never groups (its kind's capability), as in `ListView`.
  const effectiveGroupStack = resourceViewKindCapabilities(resourceView.state.view).grouping
    ? resourceView.state.groupStack : NO_GROUPS;

  const surface = useRowsResourceViewSurface({
    rows,
    query,
    columns,
    resourceView,
    groupStack: effectiveGroupStack,
    fetching,
    error,
    onListStateChange,
  });
  const catalog = useSearchCatalog({
    search: searchDeclaration,
    columns,
    query,
    rows: surface.sourceRows,
    modelMetadata: null,
    resourceView,
    defaultGroup,
    groupOptions,
    filterOptions: explicitFilterOptions,
    customFilterFields: explicitCustomFilterFields,
    textFilterField: DEFAULT_TEXT_FILTER_FIELD,
  });
  const selectedIds = selectable ? surface.selectedIds : EMPTY_SELECTED_IDS;
  const galleryRows = useGalleryRows(surface.rowModels);
  const interactive = Boolean(onRowClick || rowHref);
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
  const search = useResourceSearch({ resourceView, catalog, groupStack: effectiveGroupStack });
  // The same chrome rule as ListView: embedded rows get search and a pager only once they outgrow a page.
  const chrome = listChromeState(presentation, undefined, { ...surface.list, queryDirty: search.queryDirty });
  const toolbar: ResourceToolbarProps = {
    search, chrome, searchDeclaration, actions: toolbarActions, pager: surface.list,
    ...(gallery ? {
      view: resourceView.state.view, availableViews: ROWS_GALLERY_KINDS, onViewChange: resourceView.setView,
    } : {}),
    onPageChange: resourceView.setPage, onPageSizeChange: resourceView.setPageSize,
  };

  return (
    <ResourceListFrame
      className={className}
      presentation={presentation}
      compact={chrome.compact}
      toolbar={toolbar}
      selection={
        selectable
          ? {
              count: selectedIds.size,
              onClear: resourceView.clearSelectedIds,
              actions: selectedIds.size > 0
                ? bulkActions?.(selectedIds, resourceView.clearSelectedIds, {
                  filter: surface.mergedFilter,
                  // Scope snapshots are shared Rows; narrow to the declared row type (see bulkActions).
                  selectedRows: resourceView.selectedRows as readonly TRow[],
                  // Rows grouped in the browser are never placed: a grouped list reports null.
                  selectedGroups: selectedGroupsOf(selectedIds, effectiveGroupStack, null),
                } satisfies PlacedBulkCollection<TRow>)
                : undefined,
            }
          : undefined
      }
      error={error}
      loadingFooter={fetching && surface.rowModels.length > 0}
    >
      {gallery && resourceView.state.view === "gallery" ? (
        <GalleryView<TRow>
          rows={galleryRows.rows}
          getRowId={galleryRows.getRowId}
          imageField={gallery.image}
          titleField={gallery.title}
          subtitleField={gallery.subtitle}
          renderCard={gallery.renderCard}
          cardActions={
            rowActionSurface.hasActions ? rowActionSurface.render : undefined
          }
          cardHref={rowHref}
          onCardClick={onRowClick}
          draggableRow={draggableRow}
          selectedIds={selectedIds}
          onToggleSelected={selectable ? resourceView.toggleSelectedId : undefined}
          fetching={fetching}
          emptyContent={resolvedEmptyContent}
        />
      ) : (
        <FlatListBody
          tableLayout={tableLayout}
          headerVisibility={headerVisibility}
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
          groupStack={effectiveGroupStack}
          interactive={interactive}
          selectable={selectable}
          rowHref={rowHref}
          onRowClick={onRowClick}
          activeRowId={activeRowId}
          draggableRow={draggableRow}
          renderRowActions={
            rowActionSurface.hasActions ? rowActionSurface.render : undefined
          }
          onReorder={onReorder}
          canReorderRow={canReorderRow}
          footerRow={footerRow}
          emptyContent={resolvedEmptyContent}
          fetching={fetching}
        />
      )}
    </ResourceListFrame>
  );
}
