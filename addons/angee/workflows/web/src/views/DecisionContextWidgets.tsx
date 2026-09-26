import * as React from "react";
import * as v from "valibot";
import { Alert, Badge, Collapsible, ComparisonRows, InlineEmpty, optionalTranslation, useRecordPeek,
  type RecordPeekOpen, type WidgetDefinition, type WidgetRenderProps } from "@angee/ui";
import { useWorkflowsT } from "../i18n";
import { DecisionReferenceAction } from "./ApprovalTask";

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
const ReviewContext = v.pipe(v.object({
  facts: v.optional(v.array(Fact)),
  references: v.optional(v.union([RecordRef, v.array(RecordRef)])),
}), v.check((context) => context.facts !== undefined || context.references !== undefined));

/** Parse the framework-owned facts/references envelope retained by a Decision. */
export function decisionReviewContext(value: unknown): v.InferOutput<typeof ReviewContext> | undefined {
  const parsed = v.safeParse(ReviewContext, value);
  return parsed.success ? parsed.output : undefined;
}

/** Select an exact retained fact key, without evaluating it as a JSON pointer. */
export function decisionReviewFact(facts: unknown, pointer: string): v.InferOutput<typeof Fact> | undefined {
  const parsed = v.safeParse(v.array(Fact), facts);
  if (!parsed.success) return undefined;
  const matches = parsed.output.filter((fact) => fact.pointer === pointer);
  return matches.length === 1 ? matches[0] : undefined;
}
const Difference = v.object({
  field: v.string(), label: v.string(), left: Json, right: Json,
  changed: v.boolean(), leftRecord: v.optional(v.nullable(RecordRef)),
  rightRecord: v.optional(v.nullable(RecordRef)),
});
const Reason = v.object({ code: v.string(), parameters: v.optional(v.record(v.string(),
  v.union([v.string(), v.number(), v.boolean()])), {}) });

function InvalidContext(): React.ReactElement {
  const t = useWorkflowsT();
  return <Alert tone="warning">{t("inbox.contextInvalid")}</Alert>;
}

function RecordLine({ record, open }: {
  record: v.InferOutput<typeof RecordRef>;
  open: RecordPeekOpen;
}): React.ReactElement {
  const t = useWorkflowsT();
  const authoredLabel = record.label.trim();
  const label = authoredLabel && authoredLabel !== record.model && authoredLabel !== record.id
    ? authoredLabel : t("inbox.contextOpenRecord");
  return <div className="flex flex-wrap items-center gap-x-2 text-sm">
    <DecisionReferenceAction label={label} glyph="workflow-escalate" open={open} reference={record} />
    {record.page != null ? <span className="text-fg-muted">{t("inbox.contextPage", { page: record.page })}</span> : null}
  </div>;
}

function RecordContext({ value }: WidgetRenderProps): React.ReactElement {
  const open = useRecordPeek();
  const single = v.safeParse(RecordRef, value);
  if (single.success) return <RecordLine record={single.output} open={open} />;
  const list = v.safeParse(v.array(RecordRef), value);
  if (!list.success) return <InvalidContext />;
  return <div className="space-y-2">{list.output.map((record, index) => <RecordLine key={`${record.model}:${record.id}:${index}`} record={record} open={open} />)}</div>;
}

function FactsContext({ value }: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  const open = useRecordPeek();
  const parsed = v.safeParse(v.array(Fact), value);
  if (!parsed.success) return <InvalidContext />;
  return <div className="space-y-3">{parsed.output.map((fact, index) => <div key={`${fact.pointer}:${index}`} className="rounded-6 border border-border p-3">
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <span className="font-medium">{fact.label}</span>
      <Badge tone={fact.authority === "unverified" ? "warning" : "neutral"}>{authorityLabel(fact.authority, t)}</Badge>
    </div>
    <div className="mt-2"><ContextValue value={fact.value} /></div>
    {fact.subject || fact.evidence.length ? <div className="mt-3 space-y-1 border-t border-border-subtle pt-2">
      <p className="text-xs font-medium text-fg-muted">{t("inbox.contextEvidence")}</p>
      {uniqueRecords([...(fact.subject ? [fact.subject] : []), ...fact.evidence])
        .map((ref) => <RecordLine key={`${ref.model}:${ref.id}`} record={ref} open={open} />)}
    </div> : null}
  </div>)}</div>;
}

function DifferencesContext({ value }: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  const open = useRecordPeek();
  const parsed = v.safeParse(v.array(Difference), value);
  if (!parsed.success) return <InvalidContext />;
  return <ComparisonRows
    fieldLabel={t("inbox.contextField")}
    beforeLabel={t("inbox.contextBefore")}
    afterLabel={t("inbox.contextAfter")}
    rows={parsed.output.map((difference, index) => ({
      key: `${difference.field}:${index}`,
      label: difference.label,
      before: <ContextValue value={difference.left} />,
      after: <ContextValue value={difference.right} />,
      changed: difference.changed,
      details: difference.leftRecord || difference.rightRecord ? <div className="space-y-1 pt-2">
      <p className="text-xs font-medium text-fg-muted">{t("inbox.contextEvidence")}</p>
      {uniqueRecords([...(difference.leftRecord ? [difference.leftRecord] : []),
        ...(difference.rightRecord ? [difference.rightRecord] : [])])
        .map((ref) => <RecordLine key={`${ref.model}:${ref.id}`} record={ref} open={open} />)}
      </div> : undefined,
    }))}
  />;
}

function ReasonsContext({ value }: WidgetRenderProps): React.ReactElement {
  const t = useWorkflowsT();
  const parsed = v.safeParse(v.array(Reason), value);
  if (!parsed.success) return <InvalidContext />;
  return <div className="space-y-2">{parsed.output.map((reason, index) => <div key={`${reason.code}:${index}`} className="rounded-6 border border-border p-3">
    <div className="text-sm font-medium">{optionalTranslation(t, `decision.reason.${reason.code}`,
      Object.fromEntries(Object.entries(reason.parameters).map(([key, item]) =>
        [key, typeof item === "boolean" ? String(item) : item]))) ?? reason.code}</div>
    {Object.keys(reason.parameters).length ? <div className="mt-2"><ContextValue value={reason.parameters} /></div> : null}
  </div>)}</div>;
}

function ObjectContext({ value }: WidgetRenderProps): React.ReactElement {
  const parsed = v.safeParse(v.record(v.string(), Json), value);
  if (!parsed.success) return <InvalidContext />;
  return <StructuredContextValue value={parsed.output} />;
}

function ContextValue({ value }: { value: unknown }): React.ReactElement {
  const t = useWorkflowsT();
  if (value === null || value === undefined || value === "") {
    return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextNotProvided")} />;
  }
  if (typeof value === "boolean") return <span className="text-13 text-fg-2">{t(value ? "inbox.contextYes" : "inbox.contextNo")}</span>;
  if (typeof value === "string" || typeof value === "number") {
    return <span className="break-words text-13 text-fg-2">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextNone")} />;
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
  return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextUnavailable")} />;
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
    return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextNotProvided")} />;
  }
  if (typeof value === "boolean") return <span className="text-13 text-fg-2">{t(value ? "inbox.contextYes" : "inbox.contextNo")}</span>;
  if (typeof value === "string" || typeof value === "number") {
    return <span className="break-words text-13 text-fg-2">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextNone")} />;
    return <ol className="space-y-2 pl-5 text-13 marker:text-fg-muted">
      {keyedContextValues(value).map((item) => <li key={item.key}><ExactStructuredValue value={item.value} /></li>)}
    </ol>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextNone")} />;
    return <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-[minmax(8rem,0.45fr)_minmax(0,1fr)]">
      {entries.map(([key, item]) => <React.Fragment key={key}>
        <dt className="break-all font-mono text-xs text-fg-muted">{key}</dt>
        <dd className="min-w-0"><ExactStructuredValue value={item} /></dd>
      </React.Fragment>)}
    </dl>;
  }
  return <InlineEmpty className="justify-start p-0 text-left" label={t("inbox.contextUnavailable")} />;
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
