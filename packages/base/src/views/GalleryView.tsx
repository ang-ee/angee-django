import type { ReactElement, ReactNode } from "react";
import type { Row } from "@angee/sdk";

import { cn } from "../lib/cn";
import { dragSourceProps, type DndPayload } from "../lib/dnd";
import { Card } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { ListEmpty } from "./ListInternals";

/**
 * The card-grid View — a frameless sibling of `ListView` that renders each row
 * as a thumbnail card (image + title + subtitle). Composed inside a `DataPage`
 * which owns the toolbar, or standalone over a row array. `renderCard`
 * overrides the card body; selection mirrors the list's `selected` set.
 */
export interface GalleryViewProps<TRow extends Row = Row> {
  rows?: readonly TRow[];
  /** Field holding the thumbnail image URL. */
  imageField?: keyof TRow & string;
  /** Field holding the card title (defaults to `title`/`name`). */
  titleField?: keyof TRow & string;
  /** Field holding the secondary label. */
  subtitleField?: keyof TRow & string;
  /** Field holding the stable row id. */
  rowKey?: keyof TRow & string;
  /** Override the card body. */
  renderCard?: (row: TRow) => ReactNode;
  /** Navigation target for a card — renders it as a link (mirrors `rowHref`). */
  cardHref?: (row: TRow) => string;
  onCardClick?: (row: TRow) => void;
  /** Make a card draggable by returning its dnd payload, or `null`. */
  draggableRow?: (row: TRow) => DndPayload | null;
  selectedIds?: ReadonlySet<string>;
  onToggleSelected?: (id: string, selected: boolean) => void;
  /** Shown centered when `rows` is empty. */
  emptyMessage?: ReactNode;
  className?: string;
}

export function GalleryView<TRow extends Row = Row>({
  rows = [],
  imageField,
  titleField,
  subtitleField,
  rowKey = "id" as keyof TRow & string,
  renderCard,
  cardHref,
  onCardClick,
  draggableRow,
  selectedIds,
  onToggleSelected,
  emptyMessage = "No records.",
  className,
}: GalleryViewProps<TRow>): ReactElement {
  return (
    <div className={cn("flex-1 overflow-y-auto bg-canvas p-4", className)}>
      {rows.length === 0 ? (
        <ListEmpty>{emptyMessage}</ListEmpty>
      ) : (
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-4">
        {rows.map((row) => {
          const id = String(row[rowKey] ?? "");
          return (
            <GalleryCard
              key={id}
              row={row}
              imageField={imageField}
              titleField={titleField}
              subtitleField={subtitleField}
              renderCard={renderCard}
              href={cardHref?.(row)}
              dragPayload={draggableRow?.(row) ?? null}
              onClick={onCardClick}
              selected={selectedIds?.has(id) ?? false}
              onToggle={
                onToggleSelected
                  ? (next) => onToggleSelected(id, next)
                  : undefined
              }
            />
          );
        })}
      </div>
      )}
    </div>
  );
}

function GalleryCard<TRow extends Row>({
  row,
  imageField,
  titleField,
  subtitleField,
  renderCard,
  href,
  dragPayload,
  onClick,
  selected,
  onToggle,
}: {
  row: TRow;
  imageField?: keyof TRow & string;
  titleField?: keyof TRow & string;
  subtitleField?: keyof TRow & string;
  renderCard?: (row: TRow) => ReactNode;
  href?: string;
  dragPayload?: DndPayload | null;
  onClick?: (row: TRow) => void;
  selected: boolean;
  onToggle?: (next: boolean) => void;
}): ReactElement {
  const clickable = Boolean(href || onClick);
  const cardClass = cn(
    "group relative overflow-hidden p-0 outline-none",
    clickable &&
      "cursor-pointer hover:border-border-strong focus-visible:focus-ring",
    selected && "border-brand",
  );
  const dragProps = dragSourceProps(dragPayload ?? null);
  const title = cardTitle(row, titleField);
  // Card body (custom or default) plus the selection checkbox overlay — kept at
  // card level so a custom `renderCard` still gets selection. The checkbox stops
  // propagation, so ticking it never triggers the card's click/navigation.
  const content = (
    <>
      {renderCard ? (
        renderCard(row)
      ) : (
        <DefaultCardBody
          row={row}
          imageField={imageField}
          title={title}
          subtitleField={subtitleField}
        />
      )}
      {onToggle ? (
        <div
          className="absolute left-2 top-2 z-10 opacity-0 transition-opacity group-hover:opacity-100 data-[selected=true]:opacity-100"
          data-selected={selected || undefined}
          onClick={(event) => event.stopPropagation()}
        >
          <Checkbox
            checked={selected}
            onCheckedChange={(next) => onToggle(next)}
            aria-label={`Select ${title || "item"}`}
          />
        </div>
      ) : null}
    </>
  );

  // A href makes the card a real link (mirrors the list's `rowHref`); otherwise
  // an `onClick` makes it a button.
  if (href) {
    return (
      <Card asChild density="sm" className={cardClass}>
        <a href={href} {...dragProps}>
          {content}
        </a>
      </Card>
    );
  }
  const interactive = onClick
    ? {
        role: "button" as const,
        tabIndex: 0,
        onClick: () => onClick(row),
        onKeyDown: (event: React.KeyboardEvent) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onClick(row);
          }
        },
      }
    : {};
  return (
    <Card {...dragProps} {...interactive} density="sm" className={cardClass}>
      {content}
    </Card>
  );
}

function cardTitle<TRow extends Row>(
  row: TRow,
  titleField?: keyof TRow & string,
): string {
  const key =
    titleField ??
    (("title" in row ? "title" : "name" in row ? "name" : undefined) as
      | (keyof TRow & string)
      | undefined);
  return key ? String(row[key] ?? "") : "";
}

function DefaultCardBody<TRow extends Row>({
  row,
  imageField,
  title,
  subtitleField,
}: {
  row: TRow;
  imageField?: keyof TRow & string;
  title: string;
  subtitleField?: keyof TRow & string;
}): ReactElement {
  const image = imageField ? String(row[imageField] ?? "") : "";
  const subtitle = subtitleField ? String(row[subtitleField] ?? "") : "";

  return (
    <>
      <div className="relative aspect-square overflow-hidden bg-inset">
        {image ? (
          <img
            src={image}
            alt={title}
            loading="lazy"
            className="size-full object-cover"
          />
        ) : (
          <div className="grid size-full place-content-center text-2xl text-fg-subtle">
            {(title || "?").charAt(0).toUpperCase()}
          </div>
        )}
      </div>
      <div className="p-2">
        <h3 className="truncate text-13 font-medium text-fg">{title}</h3>
        {subtitle ? (
          <p className="truncate text-2xs text-fg-muted">{subtitle}</p>
        ) : null}
      </div>
    </>
  );
}
