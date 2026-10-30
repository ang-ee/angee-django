import { refineResourceName, useModelMetadata, type Row } from "@angee/metadata";
import { fieldRecord, recordValue, refineFieldsFromPaths, stringValue } from "@angee/refine";
import { useOne, type BaseRecord, type HttpError } from "@refinedev/core";
import { RecordReference, useRecordReference, useResolvedWidget } from "@angee/ui";
import type { ReactElement } from "react";

import { PAGE_MODEL } from "./data/documents";

const KNOWLEDGE_PAGE_FIELDS = refineFieldsFromPaths(["id", "title", "markdown.body"]);
type RowRecord = BaseRecord & Row;

/** Resolve the optional knowledge resource and show its current title and opening paragraph. */
export function PageReferencePreview(): ReactElement {
  const reference = useRecordReference();
  const metadata = useModelMetadata(PAGE_MODEL);
  const resource = metadata?.resource ?? null;
  const read = useOne<RowRecord, HttpError>({
    resource: refineResourceName(resource),
    id: reference.id,
    dataProviderName: resource?.schemaName,
    meta: { fields: KNOWLEDGE_PAGE_FIELDS },
    queryOptions: { enabled: resource !== null },
  });
  const page = recordValue(read.result);
  const title = stringValue(page?.title) ?? (reference.label || reference.id);
  const body = stringValue(fieldRecord(page, "markdown")?.body);
  const paragraph = firstMarkdownParagraph(body);
  const Preview = useResolvedWidget("markdown.preview")?.read;
  return <article className="grid min-w-0 max-w-xl gap-0.5">
    <h3 className="font-medium"><RecordReference {...reference} label={title} /></h3>
    {paragraph ? Preview
      ? <div className="text-fg-muted [&_p]:my-0 [&_p]:text-fg-muted"><Preview value={paragraph} readOnly /></div>
      : <p className="text-fg-muted">{paragraph}</p>
      : null}
  </article>;
}

/** Select the first prose block, ignoring leading ATX headings already represented by the page title. */
function firstMarkdownParagraph(body: string | null): string {
  if (!body) return "";
  const paragraph: string[] = [];
  for (const line of body.replace(/\r\n?/g, "\n").split("\n")) {
    if (!paragraph.length && (!line.trim() || /^ {0,3}#{1,6}(?:[ \t]+|$)/.test(line))) continue;
    if (!line.trim()) break;
    paragraph.push(line);
  }
  return paragraph.join("\n").trim();
}
