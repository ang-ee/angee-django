import * as React from "react";
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import type { Row as TableRowModel } from "@tanstack/react-table";
import { type ModelMetadata, type Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { Glyph } from "../../../chrome/Glyph";
import { cn } from "../../../lib/cn";
import type { Tone } from "../../../lib/tones";
import { CountBadge } from "../../../ui/badge";
import { Button } from "../../../ui/button";
import { CollapsibleIcon, CollapsiblePanel, CollapsibleRoot, CollapsibleTrigger } from "../../../ui/collapsible";
import { Skeleton, SkeletonStatus } from "../../../ui/skeleton";
import { StatusDot } from "../../../ui/status-icon";
import type { ResourceViewGroup } from "../resource-view-model";
import { type RowGroup } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import type { CardActionContext } from "../resource-view-types";
import { boardAppendRank } from "../board-ordering";
import { BoardRowCard, laneDotTone } from "./cards";
import { BOARD_SCROLL_SURFACE_CLASS } from "./view";
export const EMPTY_FOLD_OVERRIDES: ReadonlyMap<string, boolean> = new Map();

export function BoardSkeleton({
  laneCount,
  loadingLabel,
}: {
  laneCount: number;
  loadingLabel: React.ReactNode;
}): React.ReactElement {
  return (
    <SkeletonStatus
      label={loadingLabel}
      className={BOARD_SCROLL_SURFACE_CLASS}
    >
      {Array.from({ length: Math.max(1, laneCount) }, (_, laneIndex) => (
        <section
          key={laneIndex}
          aria-hidden="true"
          className="flex w-[300px] flex-none flex-col rounded-[10px] border border-border-subtle bg-inset"
        >
          <div className="sticky top-0 z-10 flex items-center gap-2 rounded-t-[10px] bg-inset px-3 pt-3 pb-2">
            <Skeleton className="size-2.5 shrink-0 rounded-full" />
            <Skeleton
              shape="text"
              size="sm"
              className={laneIndex % 2 === 0 ? "w-28 flex-1" : "w-20 flex-1"}
            />
            <Skeleton shape="text" size="sm" className="w-5" />
          </div>
          <div className="flex flex-col gap-2 px-2 pb-2">
            {Array.from({ length: 3 }, (_, cardIndex) => (
              <article
                key={cardIndex}
                className="grid gap-2 rounded-8 border border-border-subtle bg-sheet p-3 shadow-xs"
              >
                <Skeleton
                  shape="text"
                  size="md"
                  className={cardIndex % 2 === 0 ? "w-5/6" : "w-2/3"}
                />
                <div className="grid gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <Skeleton shape="text" size="sm" className="w-16" />
                    <Skeleton shape="text" size="sm" className="w-20" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Skeleton shape="text" size="sm" className="w-12" />
                    <Skeleton shape="text" size="sm" className="w-24" />
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
    </SkeletonStatus>
  );
}

export function BoardLane<TRow extends Row>({
  columns,
  group,
  groupStack,
  modelMetadata,
  groupFields,
  rowHref,
  onRowClick,
  cardActions,
  cardActionContext,
  dragEnabled,
  sortable,
  rankField,
  rankForRow,
  collapsed,
  onCollapsedChange,
  onCreateInLane,
  renderCard,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  group: RowGroup<TRow>;
  groupStack: readonly ResourceViewGroup[];
  modelMetadata?: ModelMetadata | null;
  groupFields: ReadonlySet<string>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  sortable: boolean;
  rankField?: string;
  rankForRow: (row: TableRowModel<TRow>) => number | undefined;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  onCreateInLane?: (laneId: string | null, rank?: number) => void;
  renderCard?: (row: TRow) => React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const tone = laneDotTone(group, groupStack, columns);
  const { setNodeRef, isOver } = useDroppable({
    id: `board-lane:${group.key}`,
    data: { type: "board-lane", laneId: group.key },
    disabled: !dragEnabled || group.dropDisabled === true,
  });
  const cards = (
    <div className="flex flex-col gap-2 px-2 pb-2">
      {group.rows.map((row) => (
        <BoardRowCard
          key={row.id}
          columns={columns}
          groupFields={groupFields}
          modelMetadata={modelMetadata}
          row={row}
          rowHref={rowHref}
          onRowClick={onRowClick}
          cardActions={cardActions}
          cardActionContext={cardActionContext}
          dragEnabled={dragEnabled}
          sortable={sortable}
          laneId={group.key}
          renderCard={renderCard}
        />
      ))}
      {onCreateInLane && group.dropDisabled !== true ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="w-full justify-start"
          onClick={() =>
            onCreateInLane(
              group.key === "" ? null : group.key,
              rankField ? boardAppendRank(group, rankForRow) : undefined,
            )
          }
        >
          <Glyph name="plus" />
          {t("board.addCard")}
        </Button>
      ) : null}
    </div>
  );
  return (
    <BoardLaneFrame
      ref={dragEnabled ? setNodeRef : undefined}
      label={group.label ?? t("list.allRecords")}
      count={group.rows.length}
      tone={tone}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      className={cn(
        dragEnabled && "transition-colors",
        isOver && group.dropDisabled !== true && "border-border-focus bg-brand-soft/25",
      )}
    >
      {sortable ? (
        <SortableContext items={group.rows.map((row) => row.id)} strategy={verticalListSortingStrategy}>
          {cards}
        </SortableContext>
      ) : cards}
    </BoardLaneFrame>
  );
}

/** Shared lane chrome; the collection surface owns counts, contents and expansion. */
export function BoardLaneFrame({
  label, count, tone, collapsed, onCollapsedChange, children, className, nested = false, ref,
}: {
  label: string;
  count: number;
  tone?: Tone;
  collapsed: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  children?: React.ReactNode;
  className?: string;
  nested?: boolean;
  ref?: React.Ref<HTMLElement>;
}): React.ReactElement {
  const headingId = React.useId();
  return (
    <section
      ref={ref}
      aria-labelledby={headingId}
      className={cn(
        "flex min-w-0 flex-col rounded-[10px] border border-border-subtle bg-inset",
        nested ? "w-full" : "w-[300px] flex-none",
        className,
      )}
    >
      <CollapsibleRoot variant="flush" open={!collapsed} onOpenChange={(open) => onCollapsedChange?.(!open)}>
        {/* The heading stays separate from its fold button for native semantics. */}
        <div className="sticky top-0 z-10 flex items-center gap-1 rounded-t-[10px] bg-inset px-2 pt-2 pb-1">
          {onCollapsedChange ? (
            <CollapsibleTrigger aria-labelledby={headingId} className="rounded-6 text-fg">
              <CollapsibleIcon />
            </CollapsibleTrigger>
          ) : null}
          {tone ? <StatusDot tone={tone} /> : null}
          <h3 id={headingId} className="min-w-0 flex-1 truncate text-13 font-semibold text-fg">
            {label}
          </h3>
          <CountBadge value={count} />
        </div>
        <CollapsiblePanel>{children}</CollapsiblePanel>
      </CollapsibleRoot>
    </section>
  );
}
