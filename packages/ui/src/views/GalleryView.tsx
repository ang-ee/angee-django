import { useMemo, type ReactElement, type ReactNode } from "react";
import type { Row } from "@angee/metadata";
import type { Row as TableRowModel } from "@tanstack/react-table";

import { useUiT } from "../i18n";
import { useInAppLink } from "../lib/in-app-link";
import { cn } from "../lib/cn";
import { dragSourceProps, type DndPayload } from "../lib/dnd";
import { Card, CardSelection, cardFrameVariants, type CardFrame } from "../ui/card";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
import { textRoleVariants } from "../ui/text";
import { ListEmpty } from "./resource/resource-view-list-body";
import type { CardRenderer, ListEmptyContent } from "./resource/resource-view-types";

/**
 * The card-grid View — a frameless sibling of `ListView` that renders each row
 * as a thumbnail card (image + title + subtitle). Composed inside a `ResourceList`
 * which owns the toolbar, or standalone over a row array. `renderCard`
 * overrides the card body (its context says `kind: "gallery"` and whether the
 * card is selected); selection mirrors the list's `selected` set.
 */
export interface GalleryViewProps<TRow extends Row = Row> {
  rows?: readonly TRow[];
  /** Field holding the thumbnail image URL. */
  imageField?: keyof TRow & string;
  /** Field holding the card title (defaults to `title`/`name`). */
  titleField?: keyof TRow & string;
  /** Field holding the secondary label. */
  subtitleField?: keyof TRow & string;
  /** A row's selection id; a collection passes its surface's row ids (`useGalleryRows`). Defaults to `row.id`. */
  getRowId?: (row: TRow) => string;
  /** Override the card body. */
  renderCard?: CardRenderer<TRow>;
  /** Optional actions rendered in a footer outside the card's navigation target. */
  cardActions?: (row: TRow) => ReactNode;
  /** Navigation target for a card — renders it as a link (mirrors `rowHref`). */
  cardHref?: (row: TRow) => string;
  onCardClick?: (row: TRow) => void;
  /** Make a card draggable by returning its dnd payload, or `null`. */
  draggableRow?: (row: TRow) => DndPayload | null;
  selectedIds?: ReadonlySet<string>;
  /** Toggle one card's selection; the row is the snapshot a selection owner keeps. */
  onToggleSelected?: (id: string, selected: boolean, row: TRow) => void;
  /** How a card's content meets its frame; `flush` (edge to edge) by default. */
  cardFrame?: CardFrame;
  /** Draw card-shaped placeholders while the first page is loading. */
  fetching?: boolean;
  /** Empty-state content shown when `rows` is empty. */
  emptyContent?: ListEmptyContent;
  className?: string;
}

/**
 * @kind surface
 */
export function GalleryView<TRow extends Row = Row>({
  rows = [],
  imageField,
  titleField,
  subtitleField,
  getRowId = defaultGalleryRowId,
  renderCard,
  cardActions,
  cardHref,
  onCardClick,
  draggableRow,
  selectedIds,
  onToggleSelected,
  cardFrame = "flush",
  fetching = false,
  emptyContent,
  className,
}: GalleryViewProps<TRow>): ReactElement {
  const t = useUiT();
  const resolvedEmptyContent = emptyContent ?? t("list.empty");
  return (
    <div className={cn("flex-1 overflow-y-auto bg-canvas p-4", className)}>
      {fetching && rows.length === 0 ? (
        <GallerySkeleton
          showImage={imageField !== undefined}
          loadingLabel={t("list.loading")}
        />
      ) : rows.length === 0 ? (
        <ListEmpty>{resolvedEmptyContent}</ListEmpty>
      ) : (
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-4">
        {rows.map((row) => {
          const id = getRowId(row);
          return (
            <GalleryCard
              key={id}
              row={row}
              imageField={imageField}
              titleField={titleField}
              subtitleField={subtitleField}
              renderCard={renderCard}
              actions={cardActions?.(row)}
              href={cardHref?.(row)}
              dragPayload={draggableRow?.(row) ?? null}
              onClick={onCardClick}
              frame={cardFrame}
              selected={selectedIds?.has(id) ?? false}
              onToggle={
                onToggleSelected
                  ? (next) => onToggleSelected(id, next, row)
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

function defaultGalleryRowId(row: Row): string {
  return String(row.id ?? "");
}

/**
 * A collection surface's row models as gallery rows keyed by the surface's own
 * row ids, so a card's selection is the list's selection whatever the identity field.
 * @owner GalleryView
 */
export function useGalleryRows<TRow extends Row>(
  rowModels: readonly TableRowModel<TRow>[],
): { rows: readonly TRow[]; getRowId: (row: TRow) => string } {
  return useMemo(() => {
    const ids = new Map(rowModels.map((model) => [model.original, model.id]));
    return { rows: rowModels.map((model) => model.original), getRowId: (row: TRow) => ids.get(row) ?? "" };
  }, [rowModels]);
}

function GallerySkeleton({
  showImage,
  loadingLabel,
}: {
  showImage: boolean;
  loadingLabel: ReactNode;
}): ReactElement {
  return (
    <SkeletonStatus
      loadingLabel={loadingLabel}
      className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-4"
    >
      {Array.from({ length: 8 }, (_, index) => (
        <Card
          key={index}
          aria-hidden="true"
          className={cn(cardFrameVariants({ frame: "flush" }).root(), "shadow-none")}
        >
          {showImage ? <Skeleton className="aspect-[4/3] rounded-none" /> : null}
          <div className="grid gap-2 p-3">
            <Skeleton
              shape="text"
              size="md"
              className={index % 2 === 0 ? "w-4/5" : "w-2/3"}
            />
            <Skeleton
              shape="text"
              size="sm"
              className={index % 3 === 0 ? "w-1/2" : "w-3/5"}
            />
          </div>
        </Card>
      ))}
    </SkeletonStatus>
  );
}

function GalleryCard<TRow extends Row>({
  row,
  imageField,
  titleField,
  subtitleField,
  renderCard,
  actions,
  href,
  dragPayload,
  onClick,
  frame,
  selected,
  onToggle,
}: {
  row: TRow;
  imageField?: keyof TRow & string;
  titleField?: keyof TRow & string;
  subtitleField?: keyof TRow & string;
  renderCard?: CardRenderer<TRow>;
  actions?: ReactNode;
  href?: string;
  dragPayload?: DndPayload | null;
  onClick?: (row: TRow) => void;
  frame: CardFrame;
  selected: boolean;
  onToggle?: (next: boolean) => void;
}): ReactElement {
  const t = useUiT();
  const clickable = Boolean(href || onClick);
  const dragProps = dragSourceProps(dragPayload ?? null);
  const title = cardTitle(row, titleField);
  // Selection is a sibling of the interactive body, including with custom cards.
  return (
    <GalleryCardFrame
      row={row}
      href={href}
      onClick={onClick}
      actions={actions}
      frame={frame}
      clickable={clickable}
      selected={selected}
      onToggle={onToggle}
      selectionLabel={title ? t("card.select", { title }) : t("card.selectUntitled")}
      dragProps={dragProps}
    >
      {renderCard ? renderCard(row, { kind: "gallery", selected }) : <DefaultCardBody
        row={row} imageField={imageField} title={title} subtitleField={subtitleField}
      />}
    </GalleryCardFrame>
  );
}

interface GalleryCardFrameProps<TRow extends Row> {
  row: TRow;
  href?: string;
  onClick?: (row: TRow) => void;
  actions?: ReactNode;
  frame: CardFrame;
  clickable: boolean;
  selected: boolean;
  onToggle?: (next: boolean) => void;
  selectionLabel: string;
  dragProps: ReturnType<typeof dragSourceProps>;
  children: ReactNode;
}

/** One interactive body/frame owner for cards with and without an action footer. */
function GalleryCardFrame<TRow extends Row>({
  row,
  href,
  onClick,
  actions,
  frame,
  clickable,
  selected,
  onToggle,
  selectionLabel,
  dragProps,
  children,
}: GalleryCardFrameProps<TRow>): ReactElement {
  const link = useInAppLink(href, undefined, { onFollow: () => onClick?.(row) });
  const styles = cardFrameVariants({ frame, interactive: clickable, selected });
  // The frame draws the focus ring for its body (`data-slot="card-body"`).
  const bodyClass = cn(styles.body(), "block");
  const body = href ? (
    <a href={href} data-slot="card-body" className={bodyClass} {...link}>
      {children}
    </a>
  ) : (
    <div
      {...interactiveCardBodyProps(row, onClick)}
      data-slot="card-body"
      className={bodyClass}
    >
      {children}
    </div>
  );
  const selection = onToggle ? (
    <CardSelection selected={selected} onSelectedChange={onToggle} label={selectionLabel}
      className={styles.selection()} />
  ) : null;
  return (
    <Card
      {...dragProps}
      density="sm"
      selected={selected}
      data-slot="gallery-card"
      data-frame={frame}
      className={styles.root()}
    >
      <div className={styles.row()}>
        {body}
        {selection ? <div className={styles.controls()}>{selection}</div> : null}
      </div>
      {actions ? <footer className={styles.footer()}>{actions}</footer> : null}
    </Card>
  );
}

function interactiveCardBodyProps<TRow extends Row>(
  row: TRow,
  onClick: ((row: TRow) => void) | undefined,
): React.HTMLAttributes<HTMLDivElement> {
  if (!onClick) return {};
  return {
    role: "button",
    tabIndex: 0,
    onClick: () => onClick(row),
    onKeyDown: (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      onClick(row);
    },
  };
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
          <p className={textRoleVariants({ role: "caption", truncate: true })}>{subtitle}</p>
        ) : null}
      </div>
    </>
  );
}
