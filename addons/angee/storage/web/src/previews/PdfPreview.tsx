import { useState, type ReactElement } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import {
  Button,
  EmptyState,
  Glyph,
  LoadingPanel,
  type PreviewProviderProps,
} from "@angee/ui";

import { useStorageT } from "../i18n";

// pdf.js parses in a worker; point it at the worker from the pinned `pdfjs-dist`
// (held to react-pdf's exact version, so the worker and the API never skew).
pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

/** Inline PDF viewer: one page at a time from `file.url`, with paging when the
 * document has more than one. react-pdf owns the fetch and its own
 * loading/error surfaces. */
export default function PdfPreview({ file }: PreviewProviderProps): ReactElement {
  const t = useStorageT();
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(1);

  return (
    <div className="flex h-full flex-col bg-inset">
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <Document
          file={file.url}
          onLoadSuccess={({ numPages }) => {
            setPageCount(numPages);
            setPage((current) => Math.min(current, numPages));
          }}
          loading={<LoadingPanel message={t("storage.preview.loading")} />}
          error={
            <EmptyState
              icon="file"
              title={file.name}
              description={t("storage.preview.loadError")}
            />
          }
          className="grid place-content-center"
        >
          <Page pageNumber={page} className="shadow-sm" />
        </Document>
      </div>
      {pageCount > 1 ? (
        <div className="flex items-center justify-center gap-3 border-t border-subtle bg-sheet p-2 text-13 text-fg-muted">
          <Button
            variant="ghost"
            size="iconSm"
            disabled={page <= 1}
            aria-label={t("storage.preview.pdfPrev")}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            <Glyph name="chevron-left" />
          </Button>
          <span className="tabular-nums">
            {t("storage.preview.pdfPage", {
              page: String(page),
              total: String(pageCount),
            })}
          </span>
          <Button
            variant="ghost"
            size="iconSm"
            disabled={page >= pageCount}
            aria-label={t("storage.preview.pdfNext")}
            onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
          >
            <Glyph name="chevron-right" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
