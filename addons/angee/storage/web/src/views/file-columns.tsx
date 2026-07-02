import { Badge, Glyph, formatSize, isHeicMime, isImageMime, textRoleVariants, type ListColumn } from "@angee/ui";
import type { ReactElement } from "react";

import type { StorageFileRow } from "../data/file-rows";
import { fileStage, formatDate } from "../lib/file-display";

/** Columns for the file list — name (with type glyph), type, stage, size,
 * owner, and modified date. Stays presentational; the page owns selection. `t`
 * is threaded in from the rendering component (this module is not a component). */
export function fileColumns(
  t: (key: string) => string,
): readonly ListColumn<StorageFileRow>[] {
  return [
    {
      field: "name",
      header: t("column.name"),
      render: (row) => (
        <span className="flex min-w-0 items-center gap-2">
          <Glyph decorative name={row.icon} fallbackName="file" className="text-fg-muted" />
          <span className="truncate font-medium text-fg">{row.name}</span>
        </span>
      ),
    },
    { field: "mimeLabel", header: t("column.type") },
    {
      field: "uploadState",
      header: t("column.stage"),
      render: (row) => {
        const stage = fileStage(row.uploadState, t);
        return <Badge tone={stage.tone}>{stage.label}</Badge>;
      },
    },
    {
      field: "sizeBytes",
      header: t("column.size"),
      align: "right",
      render: (row) => (
        <span className="tabular-nums text-fg-muted">{formatSize(row.sizeBytes)}</span>
      ),
    },
    { field: "owner", header: t("column.owner") },
    {
      field: "updatedAt",
      header: t("column.modified"),
      render: (row) => <span className="text-fg-muted">{formatDate(row.updatedAt)}</span>,
    },
  ];
}

/** Grid-card body for a file: an image thumbnail (READY images stream from the
 * token URL) or the type glyph, with the name and type · size beneath. HEIC is
 * an image the browser can't render in an `<img>`, so it shows the glyph here —
 * the preview pane decodes it on demand. */
export function fileGalleryCard(row: StorageFileRow): ReactElement {
  return (
    <>
      <div className="grid aspect-square place-content-center overflow-hidden bg-inset">
        {isImageMime(row.mime) && !isHeicMime(row.mime) && row.url ? (
          <img
            src={row.url}
            alt={row.name}
            loading="lazy"
            className="size-full object-cover"
          />
        ) : (
          <Glyph decorative name={row.icon} fallbackName="file" className="size-9 text-fg-subtle" />
        )}
      </div>
      <div className="p-2">
        <h3 className="truncate text-13 font-medium text-fg">{row.name}</h3>
        <p className={textRoleVariants({ role: "caption", truncate: true })}>
          {row.mimeLabel} · {formatSize(row.sizeBytes)}
        </p>
      </div>
    </>
  );
}
