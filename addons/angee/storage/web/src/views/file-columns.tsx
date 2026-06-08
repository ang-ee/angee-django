import { Badge, Glyph, formatSize, type ListColumn } from "@angee/base";

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
      return <Badge variant={stage.variant}>{stage.label}</Badge>;
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
