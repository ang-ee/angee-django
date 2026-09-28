import { useEffect, useState } from "react";

import { useRuntimeBrand } from "../runtime";
import { useBreadcrumbItems } from "./Breadcrumb";

interface DocumentTitles {
  initialTitle: string;
  publishers: Map<symbol, string>;
}

const documentTitles = new WeakMap<Document, DocumentTitles>();

function syncDocumentTitle(document: Document, titles: DocumentTitles): void {
  document.title = [...titles.publishers.values()].at(-1) ?? titles.initialTitle;
}

/** Keep the browser title in sync with the same leaf the console displays.
 * The last mounted publisher wins until it unmounts; updates keep mount order.
 */
export function DocumentTitle(): null {
  const brand = useRuntimeBrand();
  const leaf = useBreadcrumbItems().at(-1)?.label;
  const [initialTitle] = useState(() => document.title);
  const [owner] = useState(() => Symbol("document-title"));
  const base = brand?.name ?? initialTitle;
  useEffect(() => {
    const titles = documentTitles.get(document) ?? {
      initialTitle: document.title,
      publishers: new Map<symbol, string>(),
    };
    documentTitles.set(document, titles);
    titles.publishers.set(owner, initialTitle);
    return () => {
      titles.publishers.delete(owner);
      syncDocumentTitle(document, titles);
      if (titles.publishers.size === 0) documentTitles.delete(document);
    };
  }, [initialTitle, owner]);
  useEffect(() => {
    const titles = documentTitles.get(document);
    if (!titles) return;
    titles.publishers.set(owner, leaf && base ? `${leaf} · ${base}` : leaf || base);
    syncDocumentTitle(document, titles);
  }, [base, leaf, owner]);
  return null;
}
