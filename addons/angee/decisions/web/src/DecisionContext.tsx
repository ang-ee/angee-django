import { refineResourceName, useModelMetadata, type Row } from "@angee/metadata";
import { fieldRecord, recordValue, refineFieldsFromPaths, stringValue } from "@angee/refine";
import { useOne, type BaseRecord, type HttpError } from "@refinedev/core";
import type { ReactElement } from "react";
import * as v from "valibot";
import { ErrorBanner, JsonValueSchema, RecordReference, titleCase, useResolvedWidget, useUiT, type JsonValue, type RecordTargetSearch } from "@angee/ui";

import { useDecisionsT } from "./i18n";

const ReferenceSchema = v.strictObject({
  model: v.string(),
  id: v.string(),
  pointer: v.optional(v.string(), ""),
  label: v.optional(v.string(), ""),
  tab: v.nullish(v.string(), null),
  page: v.nullish(v.pipe(v.number(), v.integer()), null),
  search: v.optional(v.record(v.string(), v.nullable(v.string())), {}),
});
export const DecisionContextSchema = v.strictObject({
  reason: v.optional(v.string(), ""),
  facts: v.optional(v.array(v.strictObject({
    pointer: v.string(),
    label: v.string(),
    value: JsonValueSchema,
    authority: v.picklist(["source", "correction", "unverified"]),
    evidence: v.optional(v.array(ReferenceSchema), []),
    widget: v.nullish(v.string()),
    row: v.optional(v.record(v.string(), JsonValueSchema), {}),
  })), []),
  references: v.optional(v.array(ReferenceSchema), []),
});
type Reference = v.InferOutput<typeof ReferenceSchema>;
type RowRecord = BaseRecord & Row;

const KNOWLEDGE_PAGE_MODEL = "knowledge.Page";
const KNOWLEDGE_PAGE_FIELDS = refineFieldsFromPaths(["id", "title", "markdown.body"]);

/** Project the decisions-owned context contract with canonical record links and exact targets. */
export function DecisionContext({ context, reasonOnly = false, representedRecords = [] }: {
  context: unknown; reasonOnly?: boolean; representedRecords?: readonly string[];
}): ReactElement | null {
  const t = useDecisionsT();
  const parsed = v.safeParse(DecisionContextSchema, context);
  if (!parsed.success) return <ErrorBanner description={t("context.invalid")} />;
  if (reasonOnly) return parsed.output.reason ? <p className="mt-1 text-12 text-fg-muted">{parsed.output.reason}</p> : null;
  const { facts } = parsed.output;
  const references = parsed.output.references.filter((reference) => !representedRecords.includes(reference.id));
  if (!facts.length && !references.length) return null;
  const authorityLabels = {
    source: t("context.source"), correction: t("context.correction"), unverified: t("context.unverified"),
  };
  return <section aria-label={t("context.title")} className="min-w-0 space-y-2 text-12 [overflow-wrap:anywhere]">
    {facts.length ? <section aria-label={t("context.facts")} className="space-y-1">
      <h2 className="font-medium">{t("context.facts")}</h2>
      <dl className="space-y-1">{facts.filter((fact) => fact.value !== null && fact.value !== "").map((fact, index) => <div key={`${fact.pointer}:${index}`} className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
        <dt className="font-medium" title={authorityLabels[fact.authority]}><span>{fact.label}</span>:</dt>
        <dd className="min-w-0">
          <FactValue value={fact.value} widget={fact.widget} row={fact.row} />
          {fact.evidence.length ? <div className="ml-1 inline-block align-baseline"><References heading="h3" title={t("context.evidence")} references={fact.evidence.filter((reference) => !representedRecords.includes(reference.id))} /></div> : null}
        </dd>
      </div>)}</dl>
    </section> : null}
    <References title={t("context.references")} references={references} />
  </section>;
}

export function FactValue({ value, widget, row, relationModel, label, tab, search, options, emptyLabel = "-" }: {
  value: JsonValue; widget?: string | null; row?: Record<string, JsonValue>; relationModel?: string | null; label?: string;
  options?: readonly { value: string; description?: string | null }[]; emptyLabel?: string;
} & RecordTargetSearch): ReactElement {
  const t = useUiT();
  const definition = useResolvedWidget(widget ?? "text");
  if (relationModel && typeof value === "string") return <RecordReference model={relationModel} id={value} label={label} tab={tab} search={search} />;
  if (widget && definition) return <definition.read value={value} row={row} />;
  if (Array.isArray(value)) return <span>{value.map((item, index) => <span key={index}>{index ? "; " : ""}<FactValue value={item} /></span>)}</span>;
  if (value !== null && typeof value === "object") return <span>{Object.entries(value).filter(([, item]) => item !== null && item !== "").map(([name, item], index) =>
    <span key={name}>{index ? " · " : ""}<span className="text-fg-muted">{titleCase(name)} </span><FactValue value={item} /></span>,
  )}</span>;
  return <span>{value === null ? emptyLabel : typeof value === "boolean" ? t(value ? "list.yes" : "list.no") : options?.find((option) => option.value === value)?.description ?? value}</span>;
}

function References({ title, references, heading: Heading = "h2" }: {
  title: string; references: readonly Reference[]; heading?: "h2" | "h3";
}): ReactElement | null {
  if (!references.length) return null;
  return <section aria-label={title} className="inline-flex flex-wrap gap-1">
    <Heading className="sr-only">{title}</Heading>
    <ul className="flex min-w-0 flex-wrap gap-2">{references.map((reference, index) =>
      <li key={`${reference.model}:${reference.id}:${index}`} className="min-w-0 max-w-full">
        {reference.model === KNOWLEDGE_PAGE_MODEL
          ? <KnowledgePageReference reference={reference} />
          : <RecordReference {...reference} label={reference.label || undefined} />}
      </li>,
    )}</ul>
  </section>;
}

/** Resolve the optional knowledge resource and show its current title and opening paragraph. */
function KnowledgePageReference({ reference }: { reference: Reference }): ReactElement {
  const metadata = useModelMetadata(KNOWLEDGE_PAGE_MODEL);
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
