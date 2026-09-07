import * as React from "react";
import { ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { cn } from "../../../lib/cn";
import { Skeleton, SkeletonStatus } from "../../../ui/skeleton";
import { columnTone, type ColumnDescriptor } from "../../page";
import { GroupedScopePager } from "../GroupedScopePager";
import { snapshotFromNav } from "../grouped-navigation";
import { ListEmpty, type GroupedListItem } from "../resource-view-list-body";
import type { ResourceViewGroup } from "../resource-view-model";
import type { ListViewNavigationScope, ResourceListSnapshot } from "../resource-view-surface";
import type { CardActionContext, ListEmptyContent } from "../resource-view-types";
import { BoardRowCard } from "./cards";
import { BoardLaneFrame, BoardSkeleton } from "./lanes";
import { BOARD_SCROLL_SURFACE_CLASS } from "./view";

interface GroupedBoardBodyProps<TRow extends Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  modelMetadata: ModelMetadata | null;
  groupStack: readonly ResourceViewGroup[];
  items: readonly GroupedListItem<TRow>[];
  toggleGroup: (key: string) => void;
  setScopePage: (key: string, page: number) => void;
  setScopePageSize: (key: string, pageSize: number) => void;
  rowHref?: (row: TRow, scope?: ListViewNavigationScope) => string;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  renderCard?: (row: TRow) => React.ReactNode;
  fetching: boolean;
  error: Error | null;
  emptyContent: ListEmptyContent;
}

/** Paint the shared server group stream as lanes, preserving every scope's pager and navigation. */
export function GroupedBoardBody<TRow extends Row>({
  columns, modelMetadata, groupStack, items, toggleGroup, setScopePage, setScopePageSize,
  rowHref, onRowClick, onListStateChange, cardActions, cardActionContext, renderCard,
  fetching, error, emptyContent,
}: GroupedBoardBodyProps<TRow>): React.ReactElement | null {
  const t = useUiT();
  const groupFields = new Set(groupStack.map((group) => group.field));
  if (items.length === 0) {
    if (fetching) return <BoardSkeleton laneCount={3} loadingLabel={t("list.loading")} />;
    return error ? null : <ListEmpty className="px-3 py-8">{emptyContent}</ListEmpty>;
  }

  // Depth delimits each header's children in the surface-owned stream. Rendering
  // the same hierarchy keeps subgroup controls reachable even while collapsed.
  function renderItems(start: number, end: number): React.ReactNode[] {
    const children: React.ReactNode[] = [];
    for (let index = start; index < end; index += 1) {
      const item = items[index]!;
      switch (item.kind) {
        case "groupHeader": {
          let next = index + 1;
          while (next < end) {
            const candidate = items[next]!;
            if (candidate.kind === "groupHeader" && candidate.depth <= item.depth) break;
            next += 1;
          }
          const group = groupStack[item.depth];
          const column = columns.find((candidate) => candidate.field === group?.field);
          const tone = column?.tone && group && modelMetadata
            ? columnTone(column, ResourceQuery.from(modelMetadata).group(group).bucketIdentity(item.bucket))
            : undefined;
          children.push(
            <BoardLaneFrame key={item.bucketKey} label={item.label} count={item.count} tone={tone}
              collapsed={item.expandable && !item.expanded} nested={item.depth > 0}
              onCollapsedChange={item.expandable ? () => toggleGroup(item.bucketKey) : undefined}>
              <div className="flex flex-col gap-2 px-2 pb-2">
                {item.pager ? <GroupedScopePager pager={item.pager} label={item.label}
                  onPageChange={setScopePage} onPageSizeChange={setScopePageSize} t={t} /> : null}
                {item.expandable ? renderItems(index + 1, next) : (
                  <p className="text-13 text-fg-muted">{t("list.itemsUnavailable")}</p>
                )}
              </div>
            </BoardLaneFrame>,
          );
          index = next - 1;
          break;
        }
        case "record":
          children.push(<BoardRowCard key={item.itemKey} columns={columns} groupFields={groupFields}
            modelMetadata={modelMetadata} row={item.row}
            rowHref={rowHref ? (row) => rowHref(row, item.nav) : undefined}
            onRowClick={onRowClick}
            onRecordOpen={() => onListStateChange?.(snapshotFromNav<TRow>(item.nav))}
            cardActions={cardActions} cardActionContext={cardActionContext} renderCard={renderCard}
            dragEnabled={false} sortable={false} laneId="" />);
          break;
        case "skeleton":
          children.push(<SkeletonStatus key={item.itemKey} label={t("list.loading")} className="grid gap-2">
            {Array.from({ length: item.rowCount }, (_, row) => <Skeleton key={row} className="h-24 rounded-8" />)}
          </SkeletonStatus>);
          break;
        case "status":
          children.push(<p key={item.itemKey} role={item.tone === "danger" ? "alert" : "status"}
            className={cn("py-3 text-13", item.tone === "danger" ? "text-danger-text" : "text-fg-muted")}>
            {item.message}
          </p>);
          break;
      }
    }
    return children;
  }

  return <div className={BOARD_SCROLL_SURFACE_CLASS}>{renderItems(0, items.length)}</div>;
}
