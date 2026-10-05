import { useMemo, useState, type ReactElement } from "react";
import type { DocumentType } from "@angee/gql/console";
import * as v from "valibot";
import { DecisionCard } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import {
  Alert, Badge, Button, Chip, CountBadge, ErrorBanner, InlineEmpty, LoadingPanel, RecordActionBar, RecordIssues, RecordReference,
  SegmentedControl, StepList, optionToken, titleCase, useActiveRecordForm, useStatusTone,
  useRecordPeek, formatDateTime, type StepListItem,
} from "@angee/ui";
import { RecordTimelineDocument, RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { useStepRetryActions } from "./step-retry";
import { useRunCancelActions } from "./run-cancel";

export interface TimelineRecord { model: string; id: string }
export interface RecordTimelineProps { record: TimelineRecord | readonly TimelineRecord[] }
export type TimelineSelection = DocumentType<typeof RecordTimelineDocument>["record_timeline"];
export type TimelineData = TimelineSelection["records"];
type TimelineRun = TimelineData[number]["runs"][number];
type TimelineStep = NonNullable<TimelineRun["graph"]["nodes"][number]["step_run"]>;
type MappedStep = TimelineStep["map_steps"][number];
const EMPTY_TIMELINE: TimelineData = [];
const runSteps = (run: TimelineRun) => run.graph.nodes.flatMap(({ step_run }) => step_run ? [step_run, ...step_run.map_steps] : []);

/** One input: a record or a selection. The contributing surface chooses placement. */
export function RecordTimeline({ record }: RecordTimelineProps): ReactElement {
  const t = useWorkflowsT();
  const query = useRecordTimelineQuery(record);
  if (query.isLoading) return <LoadingPanel />;
  if (query.error) return <ErrorBanner description={t("timeline.unavailable")} />;
  return <RecordTimelineView data={query.data?.record_timeline?.records ?? EMPTY_TIMELINE} openCount={query.data?.record_timeline?.open_decision_count ?? 0} set={Array.isArray(record)} />;
}

export function useRecordTimelineQuery(record: RecordTimelineProps["record"], enabled = true) {
  const records = useMemo(() => Array.isArray(record) ? record : [record as TimelineRecord], [record]);
  return useAuthoredQuery(RecordTimelineDocument, { records }, {
    models: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"],
    records, relatedModels: [...RUN_MODELS, "decisions.Decision"], enabled: enabled && records.length > 0 && records.every((item) => Boolean(item.id)),
  });
}

/** Presentation over the same generated read, used by stories and interaction tests. */
export function RecordTimelineView({ data, openCount, set = false }: {
  data: TimelineData; openCount: number; set?: boolean;
}): ReactElement {
  const t = useWorkflowsT();
  const [grouping, setGrouping] = useState<"record" | "question">("record");
  const form = useActiveRecordForm();
  const openRecord = useRecordPeek();
  const retryActions = useStepRetryActions(true);
  const cancelActions = useRunCancelActions(data.map((entry) => entry.record_model));
  const cancelRun = (run: TimelineRun) => <RecordActionBar record={run} actions={cancelActions} />;
  const link = (model: string, id: string, label?: string) => <Chip tone="info" size="sm"><RecordReference model={model} id={id} label={label}
    onOpen={() => openRecord({ model, id, label })} /></Chip>;
  const card = (decision: TimelineData[number]["decisions"][number], id: string, compact = false, records?: readonly string[], runId?: string) =>
    <DecisionCard key={decision.id} decision={decision} selfId={id}
      compact={compact} inStep={records ? { records } : undefined}
      onEditField={form?.id === id ? form.focusField : undefined}
      actions={runId ? cancelRun(runs.find((run) => run.id === runId)!) : null} />;
  const runs = [...new Map(data.flatMap(({ runs }) => runs).map((run) => [run.id, run])).values()];
  const count = openCount;
  const active = runs.filter((run) => run.can_cancel);
  const held = (run: TimelineRun) => runSteps(run).filter((step) => step.hold && step.hold !== "decision");
  const retryStep = (step: TimelineStep | MappedStep) => !step.can_retry ? null
    : <RecordActionBar record={step} actions={retryActions} />;
  const heldRuns = (entry: TimelineData[number]) => entry.runs.filter((run) => held(run).length).map((run) =>
    <div key={run.id} className="flex flex-wrap items-center gap-1.5 rounded-6 border border-border-subtle px-2.5 py-2">
      {link("workflows.WorkflowRun", run.id, run.display_name)}<RunStatus run={run} />
      {held(run).map((step) => step.can_retry ? <div key={step.id}>{retryStep(step)}</div> : null)}
    </div>);
  if (set) {
    const entries = data.filter((entry) => entry.decisions.length || entry.runs.some((run) => held(run).length));
    const questions = new Map<string, Map<string, {
      entries: TimelineData; decision: TimelineData[number]["decisions"][number];
    }>>();
    for (const entry of entries) for (const decision of entry.decisions) {
      let group = questions.get(decision.kind);
      if (!group) { group = new Map(); questions.set(decision.kind, group); }
      const item = group.get(decision.id);
      if (item) item.entries.push(entry);
      else group.set(decision.id, { entries: [entry], decision });
    }
    return <div className="grid min-w-0 gap-4">
      <header className="grid gap-2"><h2 className="text-15 font-semibold">{t("timeline.title")} <CountBadge tone={count ? "warning" : "neutral"} value={count} /></h2>
        <p className="text-13 text-fg-muted">{t("timeline.records", { count: data.length })} · {t("timeline.decisions", { count })} · {t("timeline.heldRuns", { count: runs.filter((run) => held(run).length).length })}</p>
        <SegmentedControl<"record" | "question"> aria-label={t("timeline.groupBy")} value={grouping} onValueChange={setGrouping}
          options={[{ value: "record", label: t("timeline.byRecord") }, { value: "question", label: t("timeline.byQuestion") }]} />
        {active.map((run) => <div key={run.id}>{cancelRun(run)}</div>)}
      </header>
      {grouping === "record" ? entries.map((entry) => <section key={entry.record_id} className="grid gap-2 border-t border-border-subtle pt-3">
        {link(entry.record_model, entry.record_id)}{entry.decisions.map((decision) => card(decision, entry.record_id, true))}
        {heldRuns(entry)}
      </section>) : <>{[...questions].map(([question, items]) => <section key={question} className="grid gap-2 border-t border-border-subtle pt-3">
        <h3 className="text-13 font-semibold">{[...items.values()][0]?.decision.kind_label} <CountBadge value={items.size} tone="warning" /></h3>
        {[...items.values()].map(({ entries, decision }) => <div key={decision.id} className="grid gap-1">
          <div className="flex flex-wrap gap-1">{entries.map((entry) => <span key={entry.record_id}>{link(entry.record_model, entry.record_id)}</span>)}</div>
          {card(decision, entries[0]!.record_id, true)}
        </div>)}
      </section>)}{entries.filter((entry) => entry.runs.some((run) => held(run).length)).map((entry) =>
        <section key={entry.record_id} className="grid gap-2 border-t border-border-subtle pt-3">
          {link(entry.record_model, entry.record_id)}{heldRuns(entry)}
        </section>)}</>}
      {!entries.length ? <InlineEmpty label={t("timeline.emptySelection")} /> : null}
    </div>;
  }
  const entry = data[0];
  const single = runs.length === 1 ? runs[0] : undefined;
  const inRuns = new Set(runs.flatMap((run) => runSteps(run).flatMap((step) => step.decision ? [step.decision.id] : [])));
  return <div className="grid min-w-0 gap-4">
    <header className="grid min-w-0 gap-1.5"><div className="flex min-w-0 items-center gap-2">
      <h2 className="min-w-0 truncate text-15 font-semibold">{single?.version?.workflow?.display_name ?? t("timeline.workflows", { count: runs.length })}</h2>
      <CountBadge tone={count ? "warning" : "neutral"} value={count} title={t("timeline.decisions", { count })} />
    </div>{single ? <div className="flex flex-wrap items-center gap-2"><RunStatus run={single} />
      <time dateTime={single.created_at}>{formatDateTime(new Date(single.created_at))}</time>
      {link("workflows.WorkflowRun", single.id, t("timeline.openRun"))}</div> : <p className="text-13 text-fg-muted">{t("timeline.history")}</p>}
      {active.map((run) => <div key={run.id}>{cancelRun(run)}</div>)}
    </header>
    {runs.map((run) => <section key={run.id} className="grid min-w-0 gap-3" aria-label={run.display_name}>
      {!single ? <div className="grid gap-1 border-t border-border-subtle pt-3"><h3 className="text-13 font-semibold">{run.version?.workflow?.display_name}</h3>
        <RunStatus run={run} /><time dateTime={run.created_at}>{formatDateTime(new Date(run.created_at))}</time>
        {link("workflows.WorkflowRun", run.id, t("timeline.openRun"))}</div> : null}
      <RunSteps run={run} recordId={entry?.record_id ?? ""}
        card={(decision, id, compact, records) => card(decision, id, compact, records, run.can_cancel ? run.id : undefined)}
        link={link} retry={retryStep} />
    </section>)}
    {entry?.decisions.filter((decision) => !inRuns.has(decision.id)).map((decision) => card(decision, entry.record_id))}
    {!runs.length && !entry?.decisions.length ? <InlineEmpty label={t("timeline.empty")} /> : null}
  </div>;
}

function RunStatus({ run }: { run: TimelineRun }) {
  const t = useWorkflowsT();
  const resolveTone = useStatusTone();
  const holds = runSteps(run).flatMap((step) => step.hold ? [step.hold] : []);
  const status = optionToken(run.status);
  const state = status === "canceled" ? "stopped" : status === "failed" ? "error"
    : status === "succeeded" ? "plan_complete" : status === "running" ? "plan_running"
    : status === "waiting" ? holds.includes("error") ? "error" : holds.includes("run") ? "run"
      : holds.includes("decision") ? "decision" : "waiting" : "unknown";
  return <div className="grid gap-1"><Badge tone={state === "unknown" ? "neutral" : resolveTone(state)}>{state === "plan_complete" && run.outcome_label || t(`timeline.status.${state}`)}</Badge>
    {status === "failed" && run.failure_reason ? <p className="text-13 text-danger-text">{run.failure_reason}</p> : null}</div>;
}

function stepState(status: string): StepListItem["state"] {
  switch (status) {
    case "ready": return "planned";
    case "running": case "waiting": return "current";
    case "succeeded": return "done";
    case "failed": case "canceled": return "stopped";
    case "skipped": default: return "optional";
  }
}

const NoteTone = v.picklist(["info", "success", "warning", "danger"]);

function RunSteps({ run, recordId, card, link, retry }: {
  run: TimelineRun; recordId: string;
  card: (decision: TimelineData[number]["decisions"][number], id: string, compact?: boolean, records?: readonly string[]) => ReactElement;
  link: (model: string, id: string, label?: string) => ReactElement;
  retry: (step: TimelineStep | MappedStep) => ReactElement | null;
}) {
  const t = useWorkflowsT();
  const resolveTone = useStatusTone();
  const [expanded, setExpanded] = useState(false);
  const ordered = [...run.graph.nodes].filter((node) => !node.key.endsWith(".body")).sort((a, b) => a.rank - b.rank);
  const done = ordered.filter((node) => node.plan === "done");
  const current = ordered.filter((node) => node.plan === "current");
  const routine = (node: typeof ordered[number]) => !node.step_run?.decision && !node.step_run?.notes.length
    && optionToken(node.step_run?.status) === "succeeded";
  const items: StepListItem[] = [{ id: "trigger", state: "trigger", title: run.trigger_event?.trigger?.display_name
    ?? (run.parent_step ? t("timeline.continuedBy", { name: run.run_as?.display_name ?? t("timeline.user") })
      : optionToken(run.origin) === "manual" ? t("timeline.startedBy", { name: run.run_as?.display_name ?? t("timeline.user") }) : t("timeline.originStarted", { origin: t(`origin.${optionToken(run.origin)}`) })), timestamp: run.created_at,
    children: run.trigger_event?.record_model && run.trigger_event.record_id ? link(run.trigger_event.record_model, run.trigger_event.record_id)
      : run.subject_model && run.subject_id ? link(run.subject_model, run.subject_id) : null }];
  const folded = !expanded && runSteps(run).some((step) => optionToken(step.status) === "waiting") && done.length > 6;
  let group: typeof ordered = [];
  const details = (step: TimelineStep | MappedStep | null, title?: string | null) => {
    const records = [...new Map((step?.records ?? []).map((record) => [`${record.record_model}:${record.record_id}`, record])).values()];
    const notes = step?.notes.filter((note) => note.message !== title) ?? [];
    return <>
      {notes.length ? <RecordIssues items={notes.map((note, index) => {
        const tone = v.safeParse(NoteTone, note.tone);
        return { id: String(index), ...note, tone: tone.success ? tone.output : "info" };
      })} /> : null}
      {records.length ? <div className="flex min-w-0 flex-wrap gap-1">{records.map((record) => record.record_model && record.record_id ? <span key={record.id}>{link(record.record_model, record.record_id, record.label || undefined)}</span> : null)}</div> : null}
      {step?.child_runs.map((child) => <div key={child.id}>{link("workflows.WorkflowRun", child.id, child.display_name)}</div>)}
      {step?.hold === "run" && step.awaited_run ? <div className="text-13 font-medium text-warning-text">{t("timeline.waitingFor", { name: step.awaited_run.display_name })}{link("workflows.WorkflowRun", step.awaited_run.id, t("timeline.openRun"))}</div> : null}
      {step?.hold === "error" ? <Alert tone="danger" title={t("timeline.status.error")} actions={retry(step)}>{step.failure_reason || step.wait_reason}</Alert> : null}
      {step?.decision ? card(step.decision, recordId, false, records.flatMap((record) => record.record_id ? [record.record_id] : [])) : null}
    </>;
  };
  const flush = () => {
    if (!group.length) return;
    if (group.length > 1) items.push({ id: `fold-${group[0]!.key}`, state: "done", title: <Button variant="link" size="sm" className="h-auto px-0" onClick={() => setExpanded(true)}>{t("timeline.doneSteps", { count: group.length })}</Button>, detail: group.map(({ label }) => label).join(", ") });
    else append(group[0]!);
    group = [];
  };
  const append = (node: typeof ordered[number]) => {
    const step = node.step_run;
    const outcome = step?.outcome_label && step.outcome_label !== titleCase(step.outcome) ? step.outcome_label : null;
    const title = optionToken(step?.status) === "succeeded" && !step?.decision
      ? outcome || node.label : node.label;
    const status = optionToken(step?.status);
    items.push({ id: node.key, state: stepState(status), title,
      timestamp: step?.updated_at, tone: ["ready", "running", "waiting", "succeeded", "failed", "canceled", "skipped"].includes(status) ? resolveTone(status) : "neutral",
      children: details(step, title) });
    for (const mapped of step?.map_steps ?? []) {
      const status = optionToken(mapped.status);
      items.push({ id: mapped.id, state: stepState(status),
        title: `${mapped.node_label} · ${mapped.map_index + 1}`, outcome: mapped.outcome_label, timestamp: mapped.updated_at,
        tone: ["ready", "running", "waiting", "succeeded", "failed", "canceled", "skipped"].includes(status) ? resolveTone(status) : "neutral", children: details(mapped) });
    }
  };
  for (const node of done) {
    if (folded && routine(node)) group.push(node);
    else { flush(); append(node); }
  }
  flush(); current.forEach(append);
  for (const node of ordered.filter((node) => node.plan === "certain")) items.push({ id: node.key, state: "planned", title: node.label });
  const optional = ordered.filter((node) => node.plan === "optional");
  if (optional.length) items.push({ id: "optional", state: "optional", title: t("timeline.optional", { labels: optional.map(({ label }) => label).join(", ") }) });
  if (run.stopped_at || optionToken(run.status) === "canceled") items.push({ id: "stopped", state: "stopped", title: t("timeline.status.stopped"), timestamp: run.stopped_at ?? run.finished_at,
    detail: t("timeline.stoppedDetail") });
  return <StepList items={items} />;
}
