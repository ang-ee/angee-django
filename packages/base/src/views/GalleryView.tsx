import type { ReactElement, ReactNode } from "react";
import type { Row } from "@angee/sdk";

import { cn } from "../lib/cn";
import { Card } from "../ui/card";
import { Checkbox } from "../ui/checkbox";

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
  onCardClick?: (row: TRow) => void;
  selectedIds?: ReadonlySet<string>;
  onToggleSelected?: (id: string, selected: boolean) => void;
  className?: string;
}

export function GalleryView<TRow extends Row = Row>({
  rows = [],
  imageField,
  titleField,
  subtitleField,
  rowKey = "id" as keyof TRow & string,
  renderCard,
  onCardClick,
  selectedIds,
  onToggleSelected,
  className,
}: GalleryViewProps<TRow>): ReactElement {
  return (
    <div className={cn("flex-1 overflow-y-auto bg-canvas p-4", className)}>
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
    </div>
  );
}

function GalleryCard<TRow extends Row>({
  row,
  imageField,
  titleField,
  subtitleField,
  renderCard,
  onClick,
  selected,
  onToggle,
}: {
  row: TRow;
  imageField?: keyof TRow & string;
  titleField?: keyof TRow & string;
  subtitleField?: keyof TRow & string;
  renderCard?: (row: TRow) => ReactNode;
  onClick?: (row: TRow) => void;
  selected: boolean;
  onToggle?: (next: boolean) => void;
}): ReactElement {
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
  const cardClass = cn(
    "group overflow-hidden p-0 outline-none",
    onClick &&
      "cursor-pointer hover:border-border-strong focus-visible:focus-ring",
    selected && "border-brand",
  );

  if (renderCard) {
    return (
      <Card {...interactive} density="sm" className={cardClass}>
        {renderCard(row)}
      </Card>
    );
  }

  const titleKey =
    titleField ??
    (("title" in row ? "title" : "name" in row ? "name" : undefined) as
      | (keyof TRow & string)
      | undefined);
  const image = imageField ? String(row[imageField] ?? "") : "";
  const title = titleKey ? String(row[titleKey] ?? "") : "";
  const subtitle = subtitleField ? String(row[subtitleField] ?? "") : "";

  return (
    <Card {...interactive} density="sm" className={cardClass}>
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
        {onToggle ? (
          <div
            className="absolute left-2 top-2 opacity-0 transition-opacity group-hover:opacity-100 data-[selected=true]:opacity-100"
            data-selected={selected || undefined}
            onClick={(event) => event.stopPropagation()}
          >
            <Checkbox
              checked={selected}
              onCheckedChange={(next) => onToggle(next)}
              aria-label={`Select ${title}`}
            />
          </div>
        ) : null}
      </div>
      <div className="p-2">
        <h3 className="truncate text-13 font-medium text-fg">{title}</h3>
        {subtitle ? (
          <p className="truncate text-2xs text-fg-muted">{subtitle}</p>
        ) : null}
      </div>
    </Card>
  );
}
