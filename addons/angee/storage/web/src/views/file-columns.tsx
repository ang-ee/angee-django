import { Badge, Glyph, formatSize, isImageMime, type ListColumn } from "@angee/base";
import type { ReactElement } from "react";

import type { StorageFileRow } from "../data/file-rows";
import { fileIconName, fileStage, formatDate } from "../lib/file-display";

/** Columns for the file list — name (with type glyph), type, stage, size,
 * owner, and modified date. Stays presentational; the page owns selection. */
export const fileColumns: readonly ListColumn<StorageFileRow>[] = [
  {
    field: "name",
    header: "Name",
    render: (row) => (
      <span className="flex min-w-0 items-center gap-2">
        <Glyph decorative name={fileIconName(row.mime)} className="text-fg-muted" />
        <span className="truncate font-medium text-fg">{row.name}</span>
      </span>
    ),
  },
  { field: "mimeLabel", header: "Type" },
  {
    field: "uploadState",
    header: "Stage",
    render: (row) => {
      const stage = fileStage(row.uploadState);
      return <Badge tone={stage.tone}>{stage.label}</Badge>;
    },
  },
  {
    field: "sizeBytes",
    header: "Size",
    align: "right",
    render: (row) => (
      <span className="tabular-nums text-fg-muted">{formatSize(row.sizeBytes)}</span>
    ),
  },
  { field: "owner", header: "Owner" },
  {
    field: "updatedAt",
    header: "Modified",
    render: (row) => <span className="text-fg-muted">{formatDate(row.updatedAt)}</span>,
  },
];

/** Grid-card body for a file: an image thumbnail (READY images stream from the
 * token URL) or the type glyph, with the name and type · size beneath. */
export function fileGalleryCard(row: StorageFileRow): ReactElement {
  return (
    <>
      <div className="grid aspect-square place-content-center overflow-hidden bg-inset">
        {isImageMime(row.mime) && row.url ? (
          <img
            src={row.url}
            alt={row.name}
            loading="lazy"
            className="size-full object-cover"
          />
        ) : (
          <Glyph decorative name={fileIconName(row.mime)} className="size-9 text-fg-subtle" />
        )}
      </div>
      <div className="p-2">
        <h3 className="truncate text-13 font-medium text-fg">{row.name}</h3>
        <p className="truncate text-2xs text-fg-muted">
          {row.mimeLabel} · {formatSize(row.sizeBytes)}
        </p>
      </div>
    </>
  );
}
