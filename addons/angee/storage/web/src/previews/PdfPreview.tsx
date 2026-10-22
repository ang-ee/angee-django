import { useEffect, useRef, useState, type ReactElement } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { ErrorBanner, LoadingPanel, Pager, type PreviewProviderProps } from "@angee/ui";

import { useStorageT } from "../i18n";

// pdf.js parses in a worker; point it at the worker from the pinned `pdfjs-dist`
// (held to react-pdf's exact version, so the worker and the API never skew).
pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

/** Inline PDF viewer: one page at a time from `file.url`, with paging when the
 * document has more than one. react-pdf owns the fetch and its own
 * loading/error surfaces. */
export default function PdfPreview({ file, page: sourcePage }: PreviewProviderProps): ReactElement {
  const t = useStorageT();
  const [pageCount, setPageCount] = useState(0);
  const requestedPage = typeof sourcePage === "number" && Number.isInteger(sourcePage) && sourcePage > 0 ? sourcePage : 1;
  const [page, setPage] = useState(requestedPage);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number>();
  // Follow an externally requested page; do not re-run when the count resolves,
  // so manual paging is never clobbered on load. The effective page is clamped
  // during render instead of mirrored into state.
  useEffect(() => {
    setPage(requestedPage);
  }, [requestedPage]);
  const currentPage = pageCount ? Math.min(page, pageCount) : page;
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(1, entry.contentRect.width));
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="flex h-full flex-col bg-inset">
      <div ref={viewportRef} className="min-h-0 flex-1 overflow-auto p-4">
        <Document
          file={file.url}
          onLoadSuccess={({ numPages }) => setPageCount(numPages)}
          loading={<LoadingPanel message={t("preview.loading")} />}
          error={<ErrorBanner title={file.name} description={t("preview.loadError")} />}
          className="grid place-content-center"
        >
          <Page pageNumber={currentPage} width={width} className="shadow-sm" />
        </Document>
      </div>
      {pageCount > 1 ? (
        <nav aria-label={t("preview.pages")} className="flex items-center justify-center gap-1 border-t border-subtle bg-sheet p-1">
          <Pager mode="record" page={currentPage} pageSize={1} total={pageCount}
            previousLabel={t("preview.pdfPrev")} nextLabel={t("preview.pdfNext")}
            onPageChange={(next) => setPage(Math.min(pageCount, Math.max(1, next)))} />
        </nav>
      ) : null}
    </div>
  );
}
