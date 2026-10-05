import type { ReactElement } from "react";
import * as v from "valibot";
import { ErrorBanner, JsonValueSchema, RecordReference, titleCase, useResolvedWidget, useUiT, type JsonValue, type RecordTargetSearch } from "@angee/ui";

import { useDecisionsT } from "./i18n";

const ReferenceSchema = v.strictObject({
  model: v.string(),
  id: v.string(),
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

/** Project the decisions-owned context contract with canonical record links and exact targets. */
export function DecisionContext({ context, reasonOnly = false, representedRecords = [] }: {
  context: unknown; reasonOnly?: boolean; representedRecords?: readonly string[];
}): ReactElement | null {
  const t = useDecisionsT();
  const parsed = v.safeParse(DecisionContextSchema, context);
  if (!parsed.success) return <ErrorBanner description={t("context.invalid")} />;
  if (reasonOnly) return parsed.output.reason ? <p className="mt-1 text-xs text-fg-muted">{parsed.output.reason}</p> : null;
  const { facts } = parsed.output;
  const references = parsed.output.references.filter((reference) => !representedRecords.includes(reference.id));
  if (!facts.length && !references.length) return null;
  const authorityLabels = {
    source: t("context.source"), correction: t("context.correction"), unverified: t("context.unverified"),
  };
  return <section aria-label={t("context.title")} className="min-w-0 space-y-2 text-xs [overflow-wrap:anywhere]">
    {facts.length ? <section aria-label={t("context.facts")} className="space-y-1">
      <h2 className="font-medium">{t("context.facts")}</h2>
      <dl className="space-y-1">{facts.filter((fact) => fact.value !== null && fact.value !== "").map((fact, index) => <div key={`${fact.pointer}:${index}`} className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
        <dt className="font-medium" title={authorityLabels[fact.authority]}><span>{fact.label}</span>:</dt>
        <dd className="min-w-0">
          <FactValue value={fact.value} widget={fact.widget} row={fact.row} />
          {fact.evidence.length ? <span className="ml-1"><References heading="h3" title={t("context.evidence")} references={fact.evidence.filter((reference) => !representedRecords.includes(reference.id))} /></span> : null}
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
      <li key={`${reference.model}:${reference.id}:${index}`} className="min-w-0 max-w-full"><RecordReference {...reference} label={reference.label || undefined} /></li>,
    )}</ul>
  </section>;
}
