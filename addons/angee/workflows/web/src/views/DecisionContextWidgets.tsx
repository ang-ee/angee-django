import * as React from "react";
import * as v from "valibot";
import { Button, Glyph, JsonValueView, optionalTranslation, useRecordPeek,
  type WidgetDefinition, type WidgetRenderProps } from "@angee/ui";
import { useWorkflowsT } from "../i18n";

const Json = v.unknown();
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

function InvalidContext({ value }: WidgetRenderProps): React.ReactElement {
  return <div className="space-y-1 text-sm text-fg-muted">
    <p>Frozen review context is unavailable.</p>
    <JsonValueView value={value} readOnly />
  </div>;
}

function RecordLine({ record }: { record: v.InferOutput<typeof RecordRef> }): React.ReactElement {
  const open = useRecordPeek();
  return <div className="text-sm">
    <Button type="button" variant="link" onClick={() => open({
      model: record.model, id: record.id, label: record.label,
      tab: record.tab, page: record.page, search: record.search,
    })}><Glyph name="workflow-escalate" />{record.label || record.id}</Button>
    <span className="ml-2 text-fg-muted">{record.model} · {record.id}</span>
    {record.page != null ? <span className="ml-2 text-fg-muted">Page {record.page}</span> : null}
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
  const parsed = v.safeParse(v.array(Fact), value);
  if (!parsed.success) return <InvalidContext value={value} />;
  return <div className="space-y-3">{parsed.output.map((fact, index) => <div key={`${fact.pointer}:${index}`} className="rounded-6 border border-border p-3">
    <div className="flex justify-between gap-3 text-sm"><span className="font-medium">{fact.label}</span><span className="text-fg-muted">{fact.authority}</span></div>
    <JsonValueView value={fact.value} readOnly />
    {fact.subject ? <RecordLine record={fact.subject} /> : null}
    {fact.evidence.map((ref, item) => <RecordLine key={`${ref.model}:${ref.id}:${item}`} record={ref} />)}
  </div>)}</div>;
}

function DifferencesContext({ value }: WidgetRenderProps): React.ReactElement {
  const parsed = v.safeParse(v.array(Difference), value);
  if (!parsed.success) return <InvalidContext value={value} />;
  return <div className="space-y-3">{parsed.output.map((difference, index) => <div key={`${difference.field}:${index}`} className="rounded-6 border border-border p-3">
    <div className="text-sm font-medium">{difference.label}</div>
    <div className="grid grid-cols-2 gap-3 text-xs text-fg-muted"><span>Before</span><span>After</span></div>
    <div className="grid grid-cols-2 gap-3"><JsonValueView value={difference.left} readOnly /><JsonValueView value={difference.right} readOnly /></div>
    {difference.leftRecord ? <RecordLine record={difference.leftRecord} /> : null}
    {difference.rightRecord ? <RecordLine record={difference.rightRecord} /> : null}
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
    {Object.keys(reason.parameters).length ? <JsonValueView value={reason.parameters} readOnly /> : null}
  </div>)}</div>;
}

export const decisionContextWidgets: Readonly<Record<string, WidgetDefinition>> = {
  record: { read: RecordContext },
  facts: { read: FactsContext },
  differences: { read: DifferencesContext },
  reasons: { read: ReasonsContext },
};
