// Kanban board rendering for the data-view surface. It consumes precomputed rows
// from its parent and remains fetch-free.
import * as React from "react";
import type { Row as TableRowModel } from "@tanstack/react-table";
import { useNavigate } from "@tanstack/react-router";
import type { Row } from "@angee/sdk";

import { CountBadge, type BadgeVariant } from "../ui/badge";
import { StatusDot } from "../ui/status-icon";
import type { DataViewContextValue } from "./data-view-context";
import type { DataViewGroup } from "./data-view-model";
import {
  LIST_VIEW_SCROLL_BUDGET,
  cellContent,
  readPath,
  type RowGroup,
} from "./list-internals";
import type { ColumnDescriptor } from "./page";

const BOARD_SCROLL_STYLE: React.CSSProperties = {
  height: LIST_VIEW_SCROLL_BUDGET,
  maxHeight: LIST_VIEW_SCROLL_BUDGET,
};
const BOARD_CARD_SHELL_CLASS =
  "block w-full rounded-lg text-left text-inherit outline-none focus-visible:focus-ring";

export interface BoardViewProps<TRow extends Row = Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  groups: readonly RowGroup<TRow>[];
  dataView: DataViewContextValue;
  selectedIds: ReadonlySet<string>;
  interactive: boolean;
  emptyMessage: React.ReactNode;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}

export function BoardView<TRow extends Row = Row>(
  props: BoardViewProps<TRow>,
): React.ReactElement {
  const {
    columns,
    groups,
    dataView,
    emptyMessage,
    rowHref,
    onRowClick,
  } = props;
  return (
    <BoardRows
      columns={columns}
      groups={groups}
      groupStack={dataView.state.groupStack}
      emptyMessage={emptyMessage}
      rowHref={rowHref}
      onRowClick={onRowClick}
    />
  );
}

function BoardRows<TRow extends Row>({
  columns,
  groups,
  groupStack,
  emptyMessage,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groups: readonly RowGroup<TRow>[];
  groupStack: readonly DataViewGroup[];
  emptyMessage: React.ReactNode;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const leaves = groups.flatMap(flattenLeaves);
  const groupFields = new Set(groupStack.map((group) => group.field));
  if (leaves.every((group) => group.rows.length === 0)) {
    return <div className="px-3 py-8 text-center text-fg-muted">{emptyMessage}</div>;
  }
  // Kanban is most useful with an active group axis; with no group-by applied a single lane is shown.
  // The board renders the current page only (bounded by the page-size cap, MAX_PAGE_SIZE), grouped into lanes; no row virtualization is used here.
  return (
    <div
      className="flex gap-3 overflow-x-auto overflow-y-hidden p-3"
      style={BOARD_SCROLL_STYLE}
    >
      {leaves.map((group) => (
        <BoardLane
          key={group.key}
          columns={columns}
          group={group}
          groupStack={groupStack}
          groupFields={groupFields}
          rowHref={rowHref}
          onRowClick={onRowClick}
        />
      ))}
    </div>
  );
}

function BoardLane<TRow extends Row>({
  columns,
  group,
  groupStack,
  groupFields,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  group: RowGroup<TRow>;
  groupStack: readonly DataViewGroup[];
  groupFields: ReadonlySet<string>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const headingId = React.useId();
  const tone = laneDotTone(group, groupStack, columns);
  return (
    <section
      aria-labelledby={headingId}
      className="flex max-h-full min-h-0 w-[300px] flex-none flex-col rounded-[10px] border border-border-subtle bg-inset"
    >
      <div className="sticky top-0 z-10 flex items-center gap-2 rounded-t-[10px] bg-inset px-3 pt-3 pb-2">
        {tone ? <StatusDot tone={tone} /> : null}
        <h3
          id={headingId}
          className="min-w-0 flex-1 truncate text-13 font-semibold text-fg"
        >
          {group.label ?? "All records"}
        </h3>
        <CountBadge value={group.rows.length} />
      </div>
      <div className="flex min-h-0 flex-col gap-2 overflow-y-auto px-2 pb-2">
        {group.rows.map((row) => (
          <BoardRowCard
            key={row.id}
            columns={columns}
            groupFields={groupFields}
            row={row}
            rowHref={rowHref}
            onRowClick={onRowClick}
          />
        ))}
      </div>
    </section>
  );
}

function BoardRowCard<TRow extends Row>({
  columns,
  groupFields,
  row,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  row: TableRowModel<TRow>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const href = rowHref?.(row.original);
  const cardColumns = columns
    .filter((column) => !groupFields.has(column.field))
    .slice(0, 4);
  const [titleColumn, ...detailColumns] = cardColumns;
  return (
    <BoardCardShell
      href={href}
      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
    >
      <article className="grid gap-2 rounded-lg border border-border-subtle bg-sheet p-3 shadow-xs transition hover:-translate-y-0.5 hover:border-border hover:shadow-md">
        {titleColumn ? (
          <span className="block min-w-0 truncate text-sm font-semibold text-fg">
            {cellContent(titleColumn, row.original)}
          </span>
        ) : null}
        {detailColumns.map((column) => (
          <div
            key={column.field}
            className="flex min-w-0 items-start justify-between gap-3 text-13"
          >
            <span className="shrink-0 text-fg-muted">
              {column.header ?? column.field}
            </span>
            <span className="min-w-0 text-right text-fg">
              {cellContent(column, row.original)}
            </span>
          </div>
        ))}
      </article>
    </BoardCardShell>
  );
}

function BoardCardShell({
  href,
  onClick,
  children,
}: {
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  const navigate = useNavigate();
  const handleClick = React.useCallback(() => {
    if (href) {
      void navigate({ to: href });
      return;
    }
    onClick?.();
  }, [href, navigate, onClick]);
  return (
    <button
      type="button"
      role={href ? "link" : undefined}
      className={BOARD_CARD_SHELL_CLASS}
      onClick={handleClick}
    >
      {children}
    </button>
  );
}

function laneDotTone<TRow extends Row>(
  group: RowGroup<TRow>,
  groupStack: readonly DataViewGroup[],
  columns: readonly ColumnDescriptor<TRow>[],
): BadgeVariant | undefined {
  const groupField = groupStack[group.depth]?.field;
  const column = groupField
    ? columns.find((candidate) => candidate.field === groupField)
    : undefined;
  if (!groupField || !column?.tone) return undefined;
  const row = group.rows[0]?.original;
  const value = row ? readPath(row, groupField) : undefined;
  const label = value == null ? "" : String(value);
  return column.tone[label] ?? "default";
}

function flattenLeaves<TRow extends Row>(group: RowGroup<TRow>): RowGroup<TRow>[] {
  if (group.children.length === 0) return [group];
  return group.children.flatMap(flattenLeaves);
}
