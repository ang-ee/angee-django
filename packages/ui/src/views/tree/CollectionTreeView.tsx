import { useEffect, useMemo, useState, type ReactNode } from "react";
import { type Row } from "@angee/metadata";
import { stableSerialize } from "@angee/refine";
import { Pager } from "../../ui/pager";
import { Button } from "../../ui/button";
import { useUiT } from "../../i18n";
import { LoadingPanel } from "../../fragments/LoadingPanel";
import { Skeleton, SkeletonStatus } from "../../ui/skeleton";
import { TreeView, type TreeViewProps } from "./TreeView";
import { ResourceListFrame } from "../resource/ResourceListFrame";
import { useResourceView } from "../resource/resource-view-context";
import { ResourceViewUtilities } from "../resource/resource-view-utilities";
import { useResourceViewSurface } from "../resource/resource-view-surface";
import { useSearchCatalog } from "../resource/search/catalog";
import { useResourceSearch } from "../resource/search/use-resource-search";
import type { ResourceToolbarProps } from "../../toolbars";
import { ResourcePaneFooter } from "../../toolbars/ResourceToolbar";
import {
  useCollectionQueryBatch,
  type CollectionPageRequest,
  type CollectionSource,
} from "../resource/collection-source";
import type { ListViewProps } from "../resource/resource-view-types";

export interface CollectionTreeViewProps<TRow extends Row> extends Pick<TreeViewProps<TRow>,
  "icon" | "insertion" | "draggableRow" | "dropAccept" | "canDropOnNode" | "onNodeDrop"
>, Pick<
  ListViewProps<TRow>,
  | "resource"
  | "presentation"
  | "columns"
  | "search"
  | "filterOptions"
  | "customFilterFields"
  | "toolbarActions"
  | "toolbarWrap"
  | "pane"
  | "textFilterField"
  | "emptyContent"
> {
  source: CollectionSource<TRow>;
  rowKey: keyof TRow & string;
  parent: keyof TRow & string;
  label: keyof TRow & string;
  hasChildren: keyof TRow & string;
  badge?: keyof TRow & string;
  selectedId?: string;
  renderRow?: (row: TRow) => ReactNode;
  /** Readable detail/path retained independently of paged siblings; missing ancestors pin only its UI projection at root. */
  revealRows?: readonly TRow[];
  /** Selection carries the exact paged branch which supplied the row. */
  onRowClick?: (row: TRow, request: CollectionPageRequest) => void;
}

/** Native tree plus the same authored transport, filter toolbar and independent page state as ListView.
 * @kind surface
 */
export function CollectionTreeView<TRow extends Row>(
  props: CollectionTreeViewProps<TRow>,
) {
  const view = useResourceView();
  const t = useUiT();
  const surface = useResourceViewSurface({
    resource: props.resource,
    source: props.source,
    columns: props.columns,
    resourceView: view,
  });
  const queryKey = stableSerialize([
    view.state.filter,
    view.state.sorting,
    view.state.pagination,
  ]);
  const [expanded, setExpanded] = useState<{
    query: string;
    ids: ReadonlySet<string>;
  }>({ query: queryKey, ids: new Set() });
  const ids = useMemo(
    () => [...new Set([
      ...(expanded.query === queryKey ? expanded.ids : []),
      ...(props.revealRows ?? []).flatMap((row) => row[props.parent] ? [String(row[props.parent])] : []),
      ...(props.insertion?.parentId ? [props.insertion.parentId] : []),
    ])],
    [expanded, queryKey, props.revealRows, props.parent, props.insertion?.parentId],
  );
  const requests = useMemo(
    () =>
      ids.map((parentId) => ({
        key: parentId,
        parentId,
        filter: view.state.filter,
        order: surface.sortOrder,
        page: (view.paginationByScope[parentId]?.pageIndex ?? 0) + 1,
        pageSize:
          view.paginationByScope[parentId]?.pageSize ??
          view.state.pagination.pageSize,
      })),
    [
      ids,
      view.state.filter,
      surface.sortOrder,
      view.paginationByScope,
      view.state.pagination.pageSize,
    ],
  );
  const children = useCollectionQueryBatch(props.source.rows, requests);
  useEffect(() => {
    const clamped = requests.flatMap((request) => {
      const result = children.get(request.key);
      if (!result?.data || result.fetching || result.error) return [];
      const last = Math.max(
        0,
        Math.ceil(result.data.total / request.pageSize) - 1,
      );
      return request.page - 1 > last
        ? [{ id: request.key, pageIndex: last, pageSize: request.pageSize }]
        : [];
    });
    if (clamped.length)
      view.setPaginationByScope((current) => ({
        ...current,
        ...Object.fromEntries(clamped.map(({ id, ...page }) => [id, page])),
      }));
  }, [children, requests, view.setPaginationByScope]);
  const rows = useMemo(() => {
    const loaded = new Map<string, TRow>([
      ...surface.rows.map((row) => ({ ...row, [props.parent]: null })),
      ...[...children.values()].flatMap((result) => result.data?.rows ?? []),
    ].map((row) => [String(row[props.rowKey]), row]));
    const knownIds = new Set([
      ...loaded.keys(), ...(props.revealRows ?? []).map((row) => String(row[props.rowKey])),
    ]);
    const visible = new Map(loaded);
    for (const row of props.revealRows ?? []) {
      const id = String(row[props.rowKey]);
      const existing = loaded.get(id);
      // A detail read may know a parent outside the current sibling page. Pin
      // that orphan in this UI projection, while loaded pages own their edges.
      const parent = existing ? existing[props.parent] : row[props.parent];
      visible.set(id, { ...row, [props.parent]: parent && !knownIds.has(String(parent)) ? null : parent });
    }
    const visibleParents = new Set([...visible.values()].map((row) => String(row[props.parent] ?? "")));
    return [...visible.values()].map((row) => {
      const id = String(row[props.rowKey]);
      const branch = children.get(id);
      return branch?.data?.total === 0 && !branch.fetching && !branch.error && !visibleParents.has(id)
        ? { ...row, [props.hasChildren]: false } : row;
    });
  }, [surface.rows, children, props.parent, props.revealRows, props.rowKey, props.hasChildren]);
  const catalog = useSearchCatalog({
    query: props.source.query,
    search: props.search,
    inferOptions: false,
    modelMetadata: null,
    columns: props.columns,
    rows: surface.rows,
    resourceView: view,
    groupOptions: [],
    filterOptions: props.filterOptions,
    customFilterFields: props.customFilterFields,
    textFilterField: props.textFilterField,
  });
  const search = useResourceSearch({ resourceView: view, catalog, groupingEnabled: false });
  const toolbar: ResourceToolbarProps = {
    pane: props.pane,
    sortOptions: props.columns.flatMap((column) => column.field && column.sortable !== false
      ? [{ value: column.field, label: typeof column.header === "string" ? column.header : column.field }] : []),
    sorting: view.state.sorting, onSortingChange: view.setSorting,
    search, searchDeclaration: props.search, pager: surface.list, view: "list", availableViews: ["list"],
    onPageChange: view.setPage, onPageSizeChange: view.setPageSize,
    actions: props.toolbarActions, wrap: props.toolbarWrap,
    utilityActions: <ResourceViewUtilities modelBacked={false} value={{
      resource: props.resource, filter: view.state.filter, selectable: false,
      fields: props.columns.flatMap((column) => column.field ? [column.field] : []),
      refresh: () => void surface.list.refetch(),
    }} />,
  };
  return (
    <ResourceListFrame
      toolbar={toolbar}
      presentation={props.presentation}
      error={surface.list.error}
      onRetry={() => void surface.list.refetch()}
      summary={surface.list.summary}
      className="min-h-0"
    >
      {surface.list.fetching && surface.rows.length === 0 ? <LoadingPanel /> : <TreeView
        key={queryKey}
        className="resource-tree-scroll min-h-0 flex-1"
        rows={rows}
        rowKey={props.rowKey}
        parent={props.parent}
        label={props.label}
        hasChildren={props.hasChildren}
        badge={props.badge}
        icon={props.icon}
        insertion={props.insertion}
        draggableRow={props.draggableRow}
        dropAccept={props.dropAccept}
        canDropOnNode={props.canDropOnNode}
        onNodeDrop={props.onNodeDrop}
        revealIds={props.selectedId ? [props.selectedId] : undefined}
        selectedId={props.selectedId}
        onSelect={(row) => {
          const request = requests.find(({ key }) => children.get(key)?.data?.rows.some(
            (candidate) => candidate[props.rowKey] === row[props.rowKey],
          ));
          props.onRowClick?.(row, request ?? {
            filter: view.state.filter, order: surface.sortOrder,
            page: view.state.pagination.pageIndex + 1, pageSize: view.state.pagination.pageSize,
          });
        }}
        emptyContent={props.emptyContent}
        onExpand={(id) =>
          setExpanded((current) => ({
            query: queryKey,
            ids: new Set([
              ...(current.query === queryKey ? current.ids : []),
              id,
            ]),
          }))
        }
        renderRow={(row) => {
          const id = String(row[props.rowKey]);
          const branch = children.get(id);
          const page = view.paginationByScope[id] ?? {
            pageIndex: 0,
            pageSize: view.state.pagination.pageSize,
          };
          return (
            <span className="flex min-w-0 flex-1 items-center gap-1">
              <span className="min-w-0 flex-1 truncate">
                {props.renderRow?.(row) ?? String(row[props.label])}
              </span>
              {branch?.fetching ? <SkeletonStatus label={t("list.loading")} className="shrink-0"><Skeleton className="size-3.5 rounded-full" /></SkeletonStatus> : null}
              {branch?.error ? (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-danger-text"
                  title={branch.error.message}
                  onClick={(event) => {
                    event.stopPropagation();
                    void branch.refetch?.();
                  }}
                >
                  {t("collection.retry")}
                </Button>
              ) : null}
              {branch?.data && branch.data.total > page.pageSize ? (
                <span
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => event.stopPropagation()}
                >
                  <Pager
                    page={page.pageIndex + 1}
                    pageSize={page.pageSize}
                    total={branch.data.total}
                    onPageChange={(next) =>
                      view.setPaginationByScope((current) => ({
                        ...current,
                        [id]: { ...page, pageIndex: next - 1 },
                      }))
                    }
                  />
                </span>
              ) : null}
            </span>
          );
        }}
      />}
      {props.pane ? <ResourcePaneFooter {...toolbar} /> : null}
    </ResourceListFrame>
  );
}
