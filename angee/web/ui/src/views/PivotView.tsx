// The thin pivot body: a table renderer over the model `usePivotResourceViewSurface`
// owns. It fetches nothing — the surface emits the row edge, the column edge, the
// measured cells and both sets of subtotals; this file only paints them, composing
// the same table, measure-formatting, glyph and menu primitives the list body uses.
// Must NOT import ListView (ListView composes this, not the other way round).
import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import { textRoleVariants } from "../ui/text";
import {
  ALIGN_CLASS,
  ListEmpty,
  ListLoadingFooter,
  TABLE_SCROLL_STYLE,
  VisibleFieldsMenu,
  formatMeasure,
  measureValue,
  type GroupMeasure,
  type VisibleFieldOption,
} from "./resource-view-list-body";
import {
  pivotColumnHeaderRows,
  type PivotAxisNode,
  type PivotAxisTruncation,
  type PivotResourceViewSurface,
} from "./pivot-view-surface";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ListEmptyContent } from "./resource-view-types";
import type { AggregateBucket } from "@angee/refine";

export interface PivotViewBodyProps {
  surface: PivotResourceViewSurface;
  resourceView: ResourceViewContextValue;
  emptyContent: ListEmptyContent;
}

export function PivotViewBody({
  surface,
  resourceView,
  emptyContent,
}: PivotViewBodyProps): React.ReactElement {
  const t = useUiT();
  const {
    measures,
    availableMeasures,
    rowItems,
    columnNodes,
    columnLeaves,
    grandTotal,
  } = surface;
  const headerRows = React.useMemo(
    () => pivotColumnHeaderRows(columnNodes),
    [columnNodes],
  );
  const measureFields = React.useMemo<readonly VisibleFieldOption[]>(
    () =>
      availableMeasures.map((measure) => ({
        id: measure.columnId,
        label: measure.label,
        visible: measures.some((active) => active.columnId === measure.columnId),
        // The pivot has no cells without a measure, so the last one is pinned.
        disabled: measures.length === 1
          && measures[0]?.columnId === measure.columnId,
      })),
    [availableMeasures, measures],
  );
  const toggleMeasure = React.useCallback(
    (id: string, visible: boolean) => {
      const active = measures.map((measure) => measure.columnId);
      const next = visible
        ? availableMeasures
            .filter(
              (measure) =>
                measure.columnId === id || active.includes(measure.columnId),
            )
            .map((measure) => measure.columnId)
        : active.filter((columnId) => columnId !== id);
      resourceView.setMeasures(next);
    },
    [availableMeasures, measures, resourceView],
  );
  // The measure columns of every column leaf, plus the row-total block.
  const columnCount = 1 + (columnLeaves.length + 1) * measures.length;

  return (
    <>
      <div className="overflow-auto" style={TABLE_SCROLL_STYLE}>
        <Table>
          <TableHeader>
            {headerRows.map((cells, level) => (
              <TableRow key={`level:${level}`}>
                {level === 0 ? (
                  <PivotCornerHead
                    rowSpan={headerRows.length + 1}
                    label={surface.rowAxisLabel}
                    measureFields={measureFields}
                    onToggleMeasure={toggleMeasure}
                    onSwapAxes={surface.swapAxes}
                    swapEnabled={surface.columnStack.length > 0}
                  />
                ) : null}
                {cells.map((cell) => (
                  <PivotColumnHead
                    key={cell.node.key}
                    node={cell.node}
                    colSpan={cell.colSpan * measures.length}
                    rowSpan={cell.rowSpan}
                    onToggle={surface.toggleColumn}
                  />
                ))}
                {level === 0 ? (
                  <TableHead
                    scope="col"
                    colSpan={measures.length}
                    rowSpan={headerRows.length}
                    className="text-right"
                  >
                    {t("list.total")}
                  </TableHead>
                ) : null}
              </TableRow>
            ))}
            <TableRow>
              {headerRows.length === 0 ? (
                <PivotCornerHead
                  rowSpan={1}
                  label={surface.rowAxisLabel}
                  measureFields={measureFields}
                  onToggleMeasure={toggleMeasure}
                  onSwapAxes={surface.swapAxes}
                  swapEnabled={surface.columnStack.length > 0}
                />
              ) : null}
              {[...columnLeaves, null].map((leaf, index) =>
                measures.map((measure) => (
                  <PivotMeasureHead
                    key={`${leaf?.key ?? "total"}:${measure.columnId}`}
                    measure={measure}
                    resourceView={resourceView}
                    // The total block's measure headers carry the label only;
                    // sorting is offered once, on the leaf blocks.
                    sortable={index < columnLeaves.length}
                  />
                )),
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rowItems.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columnCount}
                  className="py-8 text-center text-fg-muted"
                >
                  {/* An empty matrix mid-fetch is not an empty resource. */}
                  {surface.fetching ? (
                    t("list.loading")
                  ) : (
                    <ListEmpty>{emptyContent}</ListEmpty>
                  )}
                </TableCell>
              </TableRow>
            ) : (
              rowItems.map((item) => (
                <TableRow key={item.node.key}>
                  <PivotRowHead node={item.node} onToggle={surface.toggleRow} />
                  {columnLeaves.map((leaf) =>
                    measures.map((measure) => (
                      <PivotCell
                        key={`${leaf.key}:${measure.columnId}`}
                        bucket={surface.cellAt(item.node.key, leaf.key)}
                        measure={measure}
                        label={t("pivot.cellLabel", {
                          row: item.node.label,
                          column: leaf.label,
                          measure: measure.label,
                        })}
                        onDrillDown={
                          surface.drilldownFilter(item.node, leaf)
                            ? () => surface.drillDown(item.node, leaf)
                            : undefined
                        }
                      />
                    )),
                  )}
                  {measures.map((measure) => (
                    <PivotCell
                      key={`total:${measure.columnId}`}
                      bucket={item.node.bucket}
                      measure={measure}
                      emphasis
                      label={t("pivot.rowTotalLabel", {
                        row: item.node.label,
                        measure: measure.label,
                      })}
                      onDrillDown={
                        surface.drilldownFilter(item.node, null)
                          ? () => surface.drillDown(item.node, null)
                          : undefined
                      }
                    />
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableHead scope="row">{t("list.total")}</TableHead>
              {columnLeaves.map((leaf) =>
                measures.map((measure) => (
                  <PivotCell
                    key={`${leaf.key}:${measure.columnId}`}
                    bucket={leaf.bucket}
                    measure={measure}
                    emphasis
                    label={t("pivot.columnTotalLabel", {
                      column: leaf.label,
                      measure: measure.label,
                    })}
                    onDrillDown={
                      surface.drilldownFilter(null, leaf)
                        ? () => surface.drillDown(null, leaf)
                        : undefined
                    }
                  />
                )),
              )}
              {measures.map((measure) => (
                <PivotCell
                  key={`grand:${measure.columnId}`}
                  bucket={grandTotal ?? undefined}
                  measure={measure}
                  emphasis
                  label={t("list.totalMeasure", { label: measure.label })}
                />
              ))}
            </TableRow>
          </TableFooter>
        </Table>
      </div>
      <PivotTruncationNotice truncations={surface.truncations} />
      {surface.fetching ? <ListLoadingFooter /> : null}
    </>
  );
}

function PivotCornerHead({
  rowSpan,
  label,
  measureFields,
  onToggleMeasure,
  onSwapAxes,
  swapEnabled,
}: {
  rowSpan: number;
  label: string;
  measureFields: readonly VisibleFieldOption[];
  onToggleMeasure: (id: string, visible: boolean) => void;
  onSwapAxes: () => void;
  swapEnabled: boolean;
}): React.ReactElement {
  const t = useUiT();
  return (
    <TableHead scope="col" rowSpan={rowSpan} sticky className="min-w-48">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-1">
        <span className="min-w-0 truncate">{label}</span>
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label={t("pivot.swapAxes")}
          disabled={!swapEnabled}
          onClick={onSwapAxes}
        >
          <Glyph name="repeat" />
        </Button>
        <VisibleFieldsMenu fields={measureFields} onToggle={onToggleMeasure} />
      </div>
    </TableHead>
  );
}

function PivotColumnHead({
  node,
  colSpan,
  rowSpan,
  onToggle,
}: {
  node: PivotAxisNode;
  colSpan: number;
  rowSpan: number;
  onToggle: (key: string) => void;
}): React.ReactElement {
  return (
    <TableHead scope="colgroup" colSpan={colSpan} rowSpan={rowSpan} className="text-center">
      <PivotAxisToggle node={node} onToggle={onToggle} />
    </TableHead>
  );
}

function PivotRowHead({
  node,
  onToggle,
}: {
  node: PivotAxisNode;
  onToggle: (key: string) => void;
}): React.ReactElement {
  return (
    <TableHead
      scope="row"
      sticky={false}
      className="font-normal text-fg"
      style={node.depth > 0
        ? { paddingLeft: `calc(0.75rem + ${node.depth * 1.25}rem)` }
        : undefined}
    >
      <PivotAxisToggle node={node} onToggle={onToggle} />
    </TableHead>
  );
}

/** An axis member's label, as an expand/collapse control when it has a level below. */
function PivotAxisToggle({
  node,
  onToggle,
}: {
  node: PivotAxisNode;
  onToggle: (key: string) => void;
}): React.ReactElement {
  if (!node.expandable) {
    return <span className="min-w-0 truncate">{node.label}</span>;
  }
  return (
    <button
      type="button"
      className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-6 outline-none hover:text-fg focus-visible:focus-ring"
      aria-expanded={node.expanded}
      onClick={() => onToggle(node.key)}
    >
      <Glyph
        name={node.expanded ? "chevron-down" : "chevron-right"}
        className="size-3.5 shrink-0 text-fg-muted"
      />
      <span className="min-w-0 truncate">{node.label}</span>
    </button>
  );
}

function PivotMeasureHead({
  measure,
  resourceView,
  sortable,
}: {
  measure: GroupMeasure;
  resourceView: ResourceViewContextValue;
  sortable: boolean;
}): React.ReactElement {
  const t = useUiT();
  const sort = resourceView.state.sort;
  const sorted = sort?.field === measure.columnId ? sort.dir : null;
  if (!sortable) {
    return (
      <TableHead scope="col" className="text-right">
        {measure.label}
      </TableHead>
    );
  }
  return (
    <TableHead
      scope="col"
      className="text-right"
      aria-sort={
        sorted === null ? "none" : sorted === "asc" ? "ascending" : "descending"
      }
    >
      <button
        type="button"
        className="inline-flex w-full items-center justify-end gap-1 rounded-6 outline-none hover:text-fg focus-visible:focus-ring"
        aria-label={
          sorted === "asc"
            ? t("list.sortAscending", { label: measure.label })
            : sorted === "desc"
              ? t("list.sortDescending", { label: measure.label })
              : t("list.sortNotSorted", { label: measure.label })
        }
        onClick={() =>
          resourceView.setSort(
            sorted === "desc"
              ? null
              : { field: measure.columnId, dir: sorted === "asc" ? "desc" : "asc" },
          )
        }
      >
        <span className="min-w-0 truncate">{measure.label}</span>
        {sorted ? (
          <Glyph
            name={sorted === "asc" ? "chevron-up" : "chevron-down"}
            className="size-3 shrink-0 text-fg-muted"
          />
        ) : null}
      </button>
    </TableHead>
  );
}

function PivotCell({
  bucket,
  measure,
  label,
  emphasis = false,
  onDrillDown,
}: {
  bucket: AggregateBucket | undefined;
  measure: GroupMeasure;
  label: string;
  emphasis?: boolean;
  onDrillDown?: () => void;
}): React.ReactElement {
  const value = bucket ? measureValue(bucket, measure) : undefined;
  const formatted = value == null ? "" : formatMeasure(value, measure);
  return (
    <TableCell
      className={cn(ALIGN_CLASS.right, emphasis ? "font-medium" : undefined)}
    >
      {formatted && onDrillDown ? (
        <button
          type="button"
          className="rounded-6 outline-none hover:underline focus-visible:focus-ring"
          aria-label={label}
          onClick={onDrillDown}
        >
          {formatted}
        </button>
      ) : (
        <span aria-label={formatted ? label : undefined}>{formatted}</span>
      )}
    </TableCell>
  );
}

/**
 * What the axis windows left out. A capped axis level is a fact the user must
 * see — a silently truncated pivot reads as a complete one.
 */
function PivotTruncationNotice({
  truncations,
}: {
  truncations: readonly PivotAxisTruncation[];
}): React.ReactElement | null {
  const t = useUiT();
  if (truncations.length === 0) return null;
  return (
    <p className={cn(textRoleVariants({ role: "meta" }), "px-3 py-2")}>
      {truncations
        .map((truncation) =>
          t(
            truncation.side === "row"
              ? "pivot.rowsTruncated"
              : "pivot.columnsTruncated",
            { loaded: truncation.loaded, total: truncation.total },
          ),
        )
        .join(" ")}
    </p>
  );
}
