import * as React from "react";
import { useDraggable } from "@dnd-kit/core";
import { useSortable } from "@dnd-kit/sortable";
import type { Row as TableRowModel } from "@tanstack/react-table";
import { useInAppLink } from "../../../lib/in-app-link";
import { type ModelMetadata, type Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { Glyph } from "../../../chrome/Glyph";
import { cn } from "../../../lib/cn";
import type { Tone } from "../../../lib/tones";
import { Card, CardSelection, cardFrameVariants, cardTitleVariants, type CardFrame } from "../../../ui/card";
import type { ResourceViewGroup } from "../resource-view-model";
import { ListCellContent, readPath, type RowGroup } from "../resource-view-list-body";
import { columnTone } from "../../page";
import type { ColumnDescriptor } from "../../page";
import type { CardActionContext, CardRenderer } from "../resource-view-types";
import { recordRepresentationValue, titleText } from "../../form/form-view-model";
import { BOARD_CARD_SHELL_CLASS } from "./view";
interface BoardRowCardProps<TRow extends Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  modelMetadata?: ModelMetadata | null;
  row: TableRowModel<TRow>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  sortable: boolean;
  /** The lane's drag-and-drop key. */
  laneId: string;
  /** The lane's group value a card renderer reads (`RowGroup.identity`). */
  laneIdentity?: string | null;
  renderCard?: CardRenderer<TRow>;
  /** Defaults to the board's `inset` frame. */
  frame?: CardFrame;
  selected?: boolean;
  /** Present when the collection is selectable: toggle this card's selection. */
  onToggleSelected?: (selected: boolean) => void;
}

export function BoardRowCard<TRow extends Row>(
  props: BoardRowCardProps<TRow>,
): React.ReactElement {
  if (props.sortable) {
    return <SortableBoardRowCard {...props} />;
  }
  return <DraggableBoardRowCard {...props} />;
}

function DraggableBoardRowCard<TRow extends Row>({
  row,
  laneId,
  ...props
}: BoardRowCardProps<TRow>): React.ReactElement {
  const drag = useDraggable({
    id: row.id,
    data: { type: "board-card", row: row.original, laneId },
    disabled: !props.dragEnabled,
  });
  return (
    <BoardRowCardContent
      {...props}
      row={row}
      laneId={laneId}
      drag={{ ...drag, transition: undefined }}
    />
  );
}

function SortableBoardRowCard<TRow extends Row>({
  row,
  laneId,
  ...props
}: BoardRowCardProps<TRow>): React.ReactElement {
  const drag = useSortable({
    id: row.id,
    data: { type: "board-card", row: row.original, laneId },
  });
  return (
    <BoardRowCardContent
      {...props}
      row={row}
      laneId={laneId}
      dragEnabled
      drag={drag}
    />
  );
}

type BoardCardDrag = Pick<
  ReturnType<typeof useSortable>,
  | "attributes"
  | "listeners"
  | "setNodeRef"
  | "setActivatorNodeRef"
  | "transform"
  | "transition"
  | "isDragging"
>;

function BoardRowCardContent<TRow extends Row>({
  columns,
  groupFields,
  modelMetadata,
  row,
  rowHref,
  onRowClick,
  onRecordOpen,
  cardActions,
  cardActionContext,
  dragEnabled,
  laneIdentity,
  renderCard,
  frame = "inset",
  selected = false,
  onToggleSelected,
  drag,
}: BoardRowCardProps<TRow> & { drag: BoardCardDrag }): React.ReactElement {
  const t = useUiT();
  const href = rowHref?.(row.original);
  const actions = cardActions?.(row.original, cardActionContext);
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = drag;
  const style = dragEnabled && transform
    ? {
        transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`,
        transition,
      }
    : undefined;
  const styles = cardFrameVariants({
    frame, handle: dragEnabled, interactive: Boolean(href || onRowClick), selected, dragging: isDragging,
  });
  // A card is named by its record representation, as the form header and the gallery are.
  const title = titleText(recordRepresentationValue(row.original, modelMetadata ?? null), "");
  const selection = onToggleSelected ? (
    <CardSelection selected={selected} onSelectedChange={onToggleSelected} className={styles.selection()}
      label={title ? t("card.select", { title }) : t("card.selectUntitled")} />
  ) : null;
  const handle = dragEnabled ? (
    <button
      ref={setActivatorNodeRef}
      type="button"
      aria-label={t("board.dragCard")}
      className={cn(
        "grid size-7 shrink-0 touch-none cursor-grab place-content-center rounded-6 text-fg-subtle outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring focus-visible:opacity-100 active:cursor-grabbing",
        styles.handle(),
      )}
      {...attributes}
      {...listeners}
    >
      <Glyph name="grip-vertical" />
    </button>
  ) : null;
  const body = (
    <div className={styles.body()}>
      <BoardCardFrame
        href={href}
        onClick={onRowClick ? () => onRowClick(row.original) : undefined}
        onRecordOpen={onRecordOpen ? () => onRecordOpen(row.original) : undefined}
      >
        {renderCard ? (
          renderCard(row.original, {
            kind: "board",
            ...(laneIdentity === undefined ? {} : { laneId: laneIdentity }),
            dragging: isDragging,
            selected,
          })
        ) : (
          <DefaultBoardCardBody
            columns={columns}
            groupFields={groupFields}
            modelMetadata={modelMetadata}
            row={row.original}
          />
        )}
      </BoardCardFrame>
    </div>
  );
  // The surface is the shared card recipe; the frame places the body and its controls.
  return (
    <Card
      ref={dragEnabled ? setNodeRef : undefined}
      style={style}
      selected={selected}
      data-slot="board-card"
      data-frame={frame}
      data-dragging={isDragging || undefined}
      className={cn("board-card-grid group/record grid", styles.root(), dragEnabled && "select-none")}
    >
      <div className={styles.row()}>
        {body}
        {selection || handle ? <div className={styles.controls()}>{selection}{handle}</div> : null}
      </div>
      {actions ? <footer className={styles.footer()}>{actions}</footer> : null}
    </Card>
  );
}

/** A default card's title line, on the shared card title role. */
const CARD_TITLE_CLASS = cn(cardTitleVariants({ density: "sm" }), "block min-w-0 truncate");

/** The default card: the first column not used for grouping as its title, then up to three detail rows. */
export function DefaultBoardCardBody<TRow extends Row>({
  columns,
  groupFields,
  modelMetadata,
  row,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  modelMetadata?: ModelMetadata | null;
  row: TRow;
}): React.ReactElement {
  const cardColumns = columns
    .filter((column) => !groupFields.has(column.field))
    .slice(0, 4);
  const [titleColumn, ...detailColumns] = cardColumns;
  return (
    <>
      {titleColumn ? (
        <span className={CARD_TITLE_CLASS}>
          <ListCellContent
            column={titleColumn}
            row={row}
            metadata={modelMetadata}
          />
        </span>
      ) : null}
      {detailColumns.map((column) => (
        <div
          key={column.field}
          className="board-card-detail-grid grid min-w-0 items-start gap-x-3 text-13"
        >
          <span className="min-w-0 truncate text-fg-muted">
            {column.header ?? column.field}
          </span>
          <span className="min-w-0 overflow-hidden text-right text-fg [overflow-wrap:anywhere] [&>*]:max-w-full">
            <ListCellContent column={column} row={row} metadata={modelMetadata} />
          </span>
        </div>
      ))}
    </>
  );
}

/** Card fields follow the list's cell renderer and omit values the row did not project. */
export function DeclaredBoardCardBody<TRow extends Row>({
  columns,
  modelMetadata,
  row,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  modelMetadata?: ModelMetadata | null;
  row: TRow;
}): React.ReactElement {
  const [title, ...details] = columns;
  return <>
    {title ? <span className={CARD_TITLE_CLASS}>
      <ListCellContent column={title} row={row} metadata={modelMetadata} />
    </span> : null}
    {details.filter((column) => {
      const value = readPath(row, column.field);
      return value !== null && value !== undefined && value !== "" && (!Array.isArray(value) || value.length > 0);
    }).map((column) => <div key={column.field} className="board-card-detail-grid grid min-w-0 items-start gap-x-3 text-13">
      <span className="min-w-0 truncate text-fg-muted">{column.header ?? column.field}</span>
      <span className="min-w-0 overflow-hidden text-right text-fg [overflow-wrap:anywhere] [&>*]:max-w-full">
        <ListCellContent column={column} row={row} metadata={modelMetadata} />
      </span>
    </div>)}
  </>;
}

function BoardCardFrame({
  href,
  onClick,
  onRecordOpen,
  children,
}: {
  href?: string;
  onClick?: () => void;
  onRecordOpen?: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  const link = useInAppLink(href, undefined, { onFollow: onRecordOpen });
  if (href) {
    return (
      <a href={href} data-slot="card-body" className={BOARD_CARD_SHELL_CLASS} {...link}>
        {children}
      </a>
    );
  }
  if (onClick) {
    return (
      <button
        type="button"
        data-slot="card-body"
        className={BOARD_CARD_SHELL_CLASS}
        onClick={() => { onRecordOpen?.(); onClick(); }}
      >
        {children}
      </button>
    );
  }
  return <div className={BOARD_CARD_SHELL_CLASS}>{children}</div>;
}

export function laneDotTone<TRow extends Row>(
  group: RowGroup<TRow>,
  groupStack: readonly ResourceViewGroup[],
  columns: readonly ColumnDescriptor<TRow>[],
): Tone | undefined {
  const groupField = groupStack[group.depth]?.field;
  const column = groupField
    ? columns.find((candidate) => candidate.field === groupField)
    : undefined;
  if (!groupField || !column) return undefined;
  const row = group.rows[0]?.original;
  const value = row ? readPath(row, groupField) : undefined;
  return columnTone(column, value);
}

export function flattenLeaves<TRow extends Row>(group: RowGroup<TRow>): RowGroup<TRow>[] {
  if (group.children.length === 0) return [group];
  return group.children.flatMap(flattenLeaves);
}
