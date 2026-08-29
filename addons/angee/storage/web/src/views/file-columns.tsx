import { Glyph, formatSize, isHeicMime, isImageMime, textRoleVariants } from "@angee/ui";
import type { ReactElement } from "react";

import type { StorageFileRow } from "../data/file-rows";

/** Grid-card body for a file: an image thumbnail (READY images stream from the
 * token URL) or the type glyph, with the name and type · size beneath. HEIC is
 * an image the browser can't render in an `<img>`, so it shows the glyph here —
 * the preview pane decodes it on demand. */
export function fileGalleryCard(row: StorageFileRow): ReactElement {
  const mime = row.mime_type?.mime_type ?? "";
  const mimeLabel = row.mime_type?.label || mime || "—";
  const icon = row.mime_type?.icon_key || "file";
  const name = row.title || row.filename;
  return (
    <>
      <div className="grid aspect-square place-content-center overflow-hidden bg-inset">
        {isImageMime(mime) && !isHeicMime(mime) && row.url ? (
          <img
            src={row.url}
            alt={name}
            loading="lazy"
            className="size-full object-cover"
          />
        ) : (
          <Glyph decorative name={icon} fallbackName="file" className="size-9 text-fg-subtle" />
        )}
      </div>
      <div className="p-2">
        <h3 className="truncate text-13 font-medium text-fg">{name}</h3>
        <p className={textRoleVariants({ role: "caption", truncate: true })}>
          {mimeLabel} · {formatSize(row.size_bytes)}
        </p>
      </div>
    </>
  );
}
