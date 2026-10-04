import type { ReactElement } from "react";
import * as v from "valibot";
import { Badge, ErrorBanner, JsonValueSchema, MetaGrid, RecordReference, titleCase, useRecordPeek, useUiT, type JsonValue, type RecordPeekOpen } from "@angee/ui";

import { useDecisionsT } from "./i18n";

const ReferenceSchema = v.strictObject({
  model: v.string(),
  id: v.string(),
  label: v.optional(v.string(), ""),
  tab: v.nullish(v.string(), null),
  page: v.nullish(v.pipe(v.number(), v.integer()), null),
  search: v.optional(v.record(v.string(), v.nullable(v.string())), {}),
});
const ContextSchema = v.strictObject({
  facts: v.optional(v.array(v.strictObject({
    pointer: v.string(),
    label: v.string(),
    value: JsonValueSchema,
    authority: v.picklist(["source", "correction", "unverified"]),
    evidence: v.optional(v.array(ReferenceSchema), []),
  })), []),
  references: v.optional(v.array(ReferenceSchema), []),
});
type Reference = v.InferOutput<typeof ReferenceSchema>;

/** Project only the decisions-owned context contract; record peeks retain native navigation. */
export function DecisionContext({ context }: { context: unknown }): ReactElement | null {
  const t = useDecisionsT();
  const openRecord = useRecordPeek();
  const parsed = v.safeParse(ContextSchema, context);
  if (!parsed.success) return <ErrorBanner description={t("context.invalid")} />;
  const { facts, references } = parsed.output;
  if (!facts.length && !references.length) return null;
  const authorityLabels = {
    source: t("context.source"), correction: t("context.correction"), unverified: t("context.unverified"),
  };
  return <section aria-label={t("context.title")} className="min-w-0 space-y-4 [overflow-wrap:anywhere]">
    {facts.length ? <section aria-label={t("context.facts")} className="space-y-3">
      <h2 className="font-semibold">{t("context.facts")}</h2>
      <dl className="space-y-4">{facts.map((fact, index) => <div key={`${fact.pointer}:${index}`} className="space-y-2">
        <dt className="flex min-w-0 flex-wrap items-center gap-2 font-medium">{fact.label}<Badge>{authorityLabels[fact.authority]}</Badge></dt>
        <dd className="space-y-2">
          <FactValue value={fact.value} />

          <References heading="h3" title={t("context.evidence")} references={fact.evidence} openRecord={openRecord} />
        </dd>
      </div>)}</dl>
    </section> : null}
    <References title={t("context.references")} references={references} openRecord={openRecord} />
  </section>;
}

function FactValue({ value }: { value: JsonValue }): ReactElement {
  const t = useUiT();
  if (Array.isArray(value)) return <ul className="list-disc space-y-1 pl-5">{value.map((item, index) =>
    <li key={index}><FactValue value={item} /></li>,
  )}</ul>;
  if (value !== null && typeof value === "object") return <MetaGrid rows={Object.entries(value).map(([name, item]) => ({
    id: name, label: titleCase(name), value: <FactValue value={item} />,
  }))} />;
  return <span>{value === null ? "-" : typeof value === "boolean" ? t(value ? "list.yes" : "list.no") : value}</span>;
}

function References({ title, references, openRecord, heading: Heading = "h2" }: {
  title: string; references: readonly Reference[]; openRecord: RecordPeekOpen; heading?: "h2" | "h3";
}): ReactElement | null {
  if (!references.length) return null;
  return <section aria-label={title} className="space-y-1">
    <Heading className="text-sm font-medium">{title}</Heading>
    <ul className="flex min-w-0 flex-wrap gap-2">{references.map((reference, index) =>
      <li key={`${reference.model}:${reference.id}:${index}`} className="min-w-0 max-w-full"><RecordReference {...reference} label={reference.label || undefined} onOpen={() => openRecord(reference)} /></li>,
    )}</ul>
  </section>;
}
