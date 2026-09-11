import { useEffect, useMemo, useState, type ReactNode } from "react";
import { type Row } from "@angee/metadata";
import { stableSerialize } from "@angee/refine";
import { Pager } from "../../ui/pager";
import { Button } from "../../ui/button";
import { useUiT } from "../../i18n";
import { LoadingPanel } from "../../fragments/LoadingPanel";
import { Spinner } from "../../ui/spinner";
import { TreeView } from "./TreeView";
import { ResourceListFrame } from "../resource/ResourceListFrame";
import { useResourceView } from "../resource/resource-view-context";
import { useResourceViewSurface } from "../resource/resource-view-surface";
import { useResourceViewToolbarInputs } from "../resource/resource-view-toolbar-inputs";
import { useResourceToolbarProps } from "../resource/resource-toolbar-props";
import {
  useCollectionQueryBatch,
  type CollectionSource,
} from "../resource/collection-source";
import type { ListViewProps } from "../resource/resource-view-types";

export interface CollectionTreeViewProps<TRow extends Row> extends Pick<
  ListViewProps<TRow>,
  | "resource"
  | "columns"
  | "filterOptions"
  | "customFilterFields"
  | "toolbarActions"
  | "toolbarWrap"
  | "textFilterField"
  | "onRowClick"
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
}

/** Native tree plus the same authored transport, filter toolbar and independent page state as ListView. */
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
    () => (expanded.query === queryKey ? [...expanded.ids] : []),
    [expanded, queryKey],
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
  const rows = useMemo(
    () => [
      ...surface.rows.map((row) => ({ ...row, [props.parent]: null })),
      ...[...children.values()].flatMap((result) => result.data?.rows ?? []),
    ],
    [surface.rows, children, props.parent],
  );
  const toolbarInputs = useResourceViewToolbarInputs({
    query: props.source.query,
    inferOptions: false,
    modelMetadata: null,
    columns: props.columns,
    rows: surface.rows,
    resourceView: view,
    list: surface.list,
    groupOptions: [],
    filterOptions: props.filterOptions,
    customFilterFields: props.customFilterFields,
    textFilterField: props.textFilterField,
  });
  const toolbar = useResourceToolbarProps({
    ...toolbarInputs,
    resourceView: view,
    textFilterField: props.textFilterField,
    groupingEnabled: false,
    view: "list",
    availableViews: ["list"],
    actions: props.toolbarActions,
    wrap: props.toolbarWrap,
  });
  return (
    <ResourceListFrame
      toolbar={toolbar}
      error={surface.list.error}
      onRetry={() => void surface.list.refetch()}
      summary={surface.list.summary}
      className="min-h-0"
    >
      {surface.list.fetching && surface.rows.length === 0 ? <LoadingPanel /> : <TreeView
        key={queryKey}
        rows={rows}
        rowKey={props.rowKey}
        parent={props.parent}
        label={props.label}
        hasChildren={props.hasChildren}
        badge={props.badge}
        selectedId={props.selectedId}
        onSelect={props.onRowClick}
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
              {branch?.fetching ? <Spinner size="sm" /> : null}
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
    </ResourceListFrame>
  );
}
