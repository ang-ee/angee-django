import * as React from "react";
import * as v from "valibot";
import { Badge, Button, Collapsible, Glyph, optionalTranslation, useRecordPeek,
  type WidgetDefinition, type WidgetRenderProps } from "@angee/ui";
import { useWorkflowsT } from "../i18n";

const Json = v.unknown();
export const DECISION_OBJECT_WIDGET = "decisionObject";
const RecordRef = v.object({
  model: v.string(), id: v.string(), label: v.optional(v.string(), ""),
  tab: v.optional(v.nullable(v.string())), page: v.optional(v.nullable(v.number())),
  search: v.optional(v.record(v.string(), v.nullable(v.string()))),
});
const Fact = v.object({
  pointer: v.string(), label: v.string(), value: Json,
  subject: v.optional(v.nullable(RecordRef)),
  authority: v.picklist(["source", "correction", "unverified"]),
  evidence: v.optional(v.array(RecordRef), []),
});
const Difference = v.object({
  field: v.string(), label: v.string(), left: Json, right: Json,
  changed: v.boolean(), leftRecord: v.optional(v.nullable(RecordRef)),
  rightRecord: v.optional(v.nullable(RecordRef)),
});
const Reason = v.object({ code: v.string(), parameters: v.optional(v.record(v.string(),
  v.union([v.string(), v.number(), v.boolean()])), {}) });

function InvalidContext(_props: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  return <div className="rounded-6 border border-warning-soft bg-warning-soft p-3 text-sm text-fg-2">
    {t("inbox.contextInvalid")}
  </div>;
}

function RecordLine({ record }: { record: v.InferOutput<typeof RecordRef> }): React.ReactElement {
  const t = useWorkflowsT();
  const open = useRecordPeek();
  const authoredLabel = record.label.trim();
  const label = authoredLabel && authoredLabel !== record.model && authoredLabel !== record.id
    ? authoredLabel : t("inbox.contextOpenRecord");
  return <div className="flex flex-wrap items-center gap-x-2 text-sm">
    <Button type="button" variant="link" onClick={() => open({
      model: record.model, id: record.id, label: record.label,
      tab: record.tab, page: record.page, search: record.search,
    })}><Glyph name="workflow-escalate" />{label}</Button>
    {record.page != null ? <span className="text-fg-muted">{t("inbox.contextPage", { page: record.page })}</span> : null}
  </div>;
}

function RecordContext({ value }: WidgetRenderProps): React.ReactElement {
  const single = v.safeParse(RecordRef, value);
  if (single.success) return <RecordLine record={single.output} />;
  const list = v.safeParse(v.array(RecordRef), value);
  if (!list.success) return <InvalidContext value={value} />;
  return <div className="space-y-2">{list.output.map((record, index) => <RecordLine key={`${record.model}:${record.id}:${index}`} record={record} />)}</div>;
}

function FactsContext({ value }: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  const parsed = v.safeParse(v.array(Fact), value);
  if (!parsed.success) return <InvalidContext value={value} />;
  return <div className="space-y-3">{parsed.output.map((fact, index) => <div key={`${fact.pointer}:${index}`} className="rounded-6 border border-border p-3">
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <span className="font-medium">{fact.label}</span>
      <Badge tone={fact.authority === "unverified" ? "warning" : "neutral"}>{authorityLabel(fact.authority, t)}</Badge>
    </div>
    <div className="mt-2"><ContextValue value={fact.value} /></div>
    {fact.subject || fact.evidence.length ? <div className="mt-3 space-y-1 border-t border-border-subtle pt-2">
      <p className="text-xs font-medium text-fg-muted">{t("inbox.contextEvidence")}</p>
      {uniqueRecords([...(fact.subject ? [fact.subject] : []), ...fact.evidence])
        .map((ref) => <RecordLine key={`${ref.model}:${ref.id}`} record={ref} />)}
    </div> : null}
  </div>)}</div>;
}

function DifferencesContext({ value }: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  const parsed = v.safeParse(v.array(Difference), value);
  if (!parsed.success) return <InvalidContext value={value} />;
  return <div className="space-y-3">{parsed.output.map((difference, index) => <div key={`${difference.field}:${index}`} className="rounded-6 border border-border p-3">
    <div className="text-sm font-medium">{difference.label}</div>
    <div className="grid grid-cols-2 gap-3 text-xs text-fg-muted"><span>{t("inbox.contextBefore")}</span><span>{t("inbox.contextAfter")}</span></div>
    <div className="grid grid-cols-2 gap-3"><ContextValue value={difference.left} /><ContextValue value={difference.right} /></div>
    {difference.leftRecord || difference.rightRecord ? <div className="mt-2 space-y-1 border-t border-border-subtle pt-2">
      <p className="text-xs font-medium text-fg-muted">{t("inbox.contextEvidence")}</p>
      {uniqueRecords([...(difference.leftRecord ? [difference.leftRecord] : []),
        ...(difference.rightRecord ? [difference.rightRecord] : [])])
        .map((ref) => <RecordLine key={`${ref.model}:${ref.id}`} record={ref} />)}
    </div> : null}
  </div>)}</div>;
}

function ReasonsContext({ value }: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  const parsed = v.safeParse(v.array(Reason), value);
  if (!parsed.success) return <InvalidContext value={value} />;
  return <div className="space-y-2">{parsed.output.map((reason, index) => <div key={`${reason.code}:${index}`} className="rounded-6 border border-border p-3">
    <div className="text-sm font-medium">{optionalTranslation(t, `decision.reason.${reason.code}`,
      Object.fromEntries(Object.entries(reason.parameters).map(([key, item]) =>
        [key, typeof item === "boolean" ? String(item) : item]))) ?? reason.code}</div>
    {Object.keys(reason.parameters).length ? <div className="mt-2"><ContextValue value={reason.parameters} /></div> : null}
  </div>)}</div>;
}

function ObjectContext({ value }: WidgetRenderProps): React.ReactElement {
  const parsed = v.safeParse(v.record(v.string(), Json), value);
  if (!parsed.success) return <InvalidContext value={value} />;
  return <StructuredContextValue value={parsed.output} />;
}

function ContextValue({ value }: { value: unknown }): React.ReactElement {
  const t = useWorkflowsT();
  if (value === null || value === undefined || value === "") {
    return <span className="text-13 text-fg-muted">{t("inbox.contextNotProvided")}</span>;
  }
  if (typeof value === "boolean") return <span className="text-13 text-fg-2">{t(value ? "inbox.contextYes" : "inbox.contextNo")}</span>;
  if (typeof value === "string" || typeof value === "number") {
    return <span className="break-words text-13 text-fg-2">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-13 text-fg-muted">{t("inbox.contextNone")}</span>;
    if (value.some((item) => item !== null && typeof item === "object")) {
      return <StructuredContextValue value={value} />;
    }
    return <ul className="space-y-1 pl-4 text-13 marker:text-fg-muted">
      {keyedContextValues(value).map((item) => <li key={item.key}><ContextValue value={item.value} /></li>)}
    </ul>;
  }
  if (typeof value === "object") {
    return <StructuredContextValue value={value} />;
  }
  return <span className="text-13 text-fg-muted">{t("inbox.contextUnavailable")}</span>;
}

/** Keep exact retained structured values available without flooding the review surface. */
function StructuredContextValue({ value }: { value: unknown }): React.ReactElement {
  const t = useWorkflowsT();
  return <Collapsible variant="section">
    <Collapsible.Trigger><Collapsible.Icon />{t("inbox.contextDetails")}</Collapsible.Trigger>
    <Collapsible.Panel><div className="pt-2"><ExactStructuredValue value={value} /></div></Collapsible.Panel>
  </Collapsible>;
}

function ExactStructuredValue({ value }: { value: unknown }): React.ReactElement {
  const t = useWorkflowsT();
  if (value === null || value === undefined || value === "") {
    return <span className="text-13 text-fg-muted">{t("inbox.contextNotProvided")}</span>;
  }
  if (typeof value === "boolean") return <span className="text-13 text-fg-2">{t(value ? "inbox.contextYes" : "inbox.contextNo")}</span>;
  if (typeof value === "string" || typeof value === "number") {
    return <span className="break-words text-13 text-fg-2">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-13 text-fg-muted">{t("inbox.contextNone")}</span>;
    return <ol className="space-y-2 pl-5 text-13 marker:text-fg-muted">
      {keyedContextValues(value).map((item) => <li key={item.key}><ExactStructuredValue value={item.value} /></li>)}
    </ol>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <span className="text-13 text-fg-muted">{t("inbox.contextNone")}</span>;
    return <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-[minmax(8rem,0.45fr)_minmax(0,1fr)]">
      {entries.map(([key, item]) => <React.Fragment key={key}>
        <dt className="break-all font-mono text-xs text-fg-muted">{key}</dt>
        <dd className="min-w-0"><ExactStructuredValue value={item} /></dd>
      </React.Fragment>)}
    </dl>;
  }
  return <span className="text-13 text-fg-muted">{t("inbox.contextUnavailable")}</span>;
}

function uniqueRecords(records: readonly v.InferOutput<typeof RecordRef>[]): v.InferOutput<typeof RecordRef>[] {
  return [...new Map(records.map((record) => [`${record.model}:${record.id}`, record])).values()];
}

function contextValueKey(value: unknown): string {
  if (value === null) return "null";
  if (typeof value !== "object") return `${typeof value}:${String(value)}`;
  return `structured:${JSON.stringify(value)}`;
}

function keyedContextValues(values: readonly unknown[]): { key: string; value: unknown }[] {
  const counts = new Map<string, number>();
  return values.map((value) => {
    const content = contextValueKey(value);
    const occurrence = counts.get(content) ?? 0;
    counts.set(content, occurrence + 1);
    return { key: `${content}:${occurrence}`, value };
  });
}

function authorityLabel(authority: v.InferOutput<typeof Fact>["authority"], t: ReturnType<typeof useWorkflowsT>): string {
  return t({
    source: "inbox.authoritySource",
    correction: "inbox.authorityCorrection",
    unverified: "inbox.authorityUnverified",
  }[authority]);
}

export const decisionContextWidgets: Readonly<Record<string, WidgetDefinition>> = {
  record: { read: RecordContext },
  facts: { read: FactsContext },
  differences: { read: DifferencesContext },
  reasons: { read: ReasonsContext },
  [DECISION_OBJECT_WIDGET]: { read: ObjectContext },
};
