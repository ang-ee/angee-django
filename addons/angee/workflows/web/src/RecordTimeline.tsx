import { useMemo, useState, type ReactElement, type ReactNode } from "react";
import type { DocumentType } from "@angee/gql/console";
import * as v from "valibot";
import { DecisionCard } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { Alert, Button, CountBadge, ErrorBanner, InlineEmpty, LoadingPanel, PaneToolbar, RecordActionBar, RecordIssues, RecordReference, SegmentedControl, StatusBadge, StepList, optionLabel, optionToken, titleCase, useActiveRecordForm, useEnumOptions, useStatusTone, formatDateTime, textRoleVariants, type StepListItem } from "@angee/ui";
import { RecordTimelineDocument, RUN_MODEL, RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { useStepRetryActions } from "./step-retry";
import { useRunCancelActions } from "./run-cancel";

export interface TimelineRecord { model: string; id: string }
export interface RecordTimelineProps { record: TimelineRecord | readonly TimelineRecord[]; /** Show this run alone (the drill-in's target). */ runId?: string }
export type TimelineSelection = DocumentType<typeof RecordTimelineDocument>["record_timeline"];
export type TimelineData = TimelineSelection["records"];
export type TimelineDecision = TimelineData[number]["decisions"][number];
type TimelineRun = TimelineData[number]["runs"][number];
type TimelineStep = NonNullable<TimelineRun["graph"]["nodes"][number]["step_run"]>;
type MappedStep = TimelineStep["map_steps"][number];
export interface TimelineNote { tone: string; message: string }
const EMPTY_TIMELINE: TimelineData = [];
const runSteps = (run: TimelineRun) => run.graph.nodes.flatMap(({ step_run }) => step_run ? [step_run, ...step_run.map_steps] : []);

/** One input: a record or a selection. The contributing surface chooses placement. */
export function RecordTimeline({ record, runId }: RecordTimelineProps): ReactElement {
  const t = useWorkflowsT();
  const query = useRecordTimelineQuery(record);
  const records = query.data?.record_timeline?.records ?? EMPTY_TIMELINE;
  const data = useMemo(
    () => runId ? records.map((entry) => ({ ...entry, runs: entry.runs.filter((run) => run.id === runId), decisions: [] })) : records,
    [records, runId],
  );
  if (query.isLoading) return <LoadingPanel />;
  if (query.error) return <ErrorBanner description={t("timeline.unavailable")} />;
  return <RecordTimelineView data={data} openCount={query.data?.record_timeline?.open_decision_count ?? 0} set={Array.isArray(record)} drillIn={Boolean(runId)} />;
}

export function useRecordTimelineQuery(record: RecordTimelineProps["record"], enabled = true, runLimit = 20) {
  const records = useMemo(() => Array.isArray(record) ? record : [record as TimelineRecord], [record]);
  return useAuthoredQuery(RecordTimelineDocument, { records, runLimit }, {
    models: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"],
    records, relatedModels: [...RUN_MODELS, "decisions.Decision"], enabled: enabled && records.length > 0 && records.every((item) => Boolean(item.id)),
  });
}

/** The one waiting-decision rendering shared by RecordTimeline and Activity. */
export function RecordTimelineDecisionCard({ decision, recordId, compact = false, records, actions }: {
  decision: TimelineDecision;
  recordId: string;
  compact?: boolean;
  records?: readonly string[];
  actions?: ReactNode;
}): ReactElement {
  const form = useActiveRecordForm();
  return <DecisionCard decision={decision} selfId={recordId} compact={compact}
    inStep={records ? { records } : undefined}
    onEditField={form?.id === recordId ? form.focusField : undefined}
    actions={actions} />;
}

/** Presentation over the same generated read, used by stories and interaction tests.
 *  As the aside's run drill-in (`drillIn`) the host names the pane, so the run's
 *  status and verbs ride the pane toolbar instead of a header of their own. */
export function RecordTimelineView({ data, openCount, set = false, drillIn = false }: {
  data: TimelineData; openCount: number; set?: boolean; drillIn?: boolean;
}): ReactElement {
  const t = useWorkflowsT();
  const [grouping, setGrouping] = useState<"record" | "question">("record");
  const retryActions = useStepRetryActions(true);
  const cancelActions = useRunCancelActions(data.map((entry) => entry.record_model));
  const cancelRun = (run: TimelineRun) => <RecordActionBar record={run} actions={cancelActions} />;
  const link = (model: string, id: string, label?: string) => <RecordReference model={model} id={id} label={label} />;
  const runs = [...new Map(data.flatMap(({ runs }) => runs).map((run) => [run.id, run])).values()];
  const card = (decision: TimelineDecision, id: string, compact = false, records?: readonly string[], runId?: string) =>
    <RecordTimelineDecisionCard key={decision.id} decision={decision} recordId={id}
      compact={compact} records={records}
      actions={runId ? cancelRun(runs.find((run) => run.id === runId)!) : null} />;
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
      <header className="grid gap-2"><h2 className={textRoleVariants({ role: "title" })}>{t("runs.title")} <CountBadge tone={count ? "warning" : "neutral"} value={count} /></h2>
        <p className={textRoleVariants({ role: "meta" })}>{t("timeline.records", { count: data.length })} · {t("timeline.decisions", { count })} · {t("timeline.heldRuns", { count: runs.filter((run) => held(run).length).length })}</p>
        <SegmentedControl<"record" | "question"> aria-label={t("timeline.groupBy")} value={grouping} onValueChange={setGrouping}
          options={[{ value: "record", label: t("timeline.byRecord") }, { value: "question", label: t("timeline.byQuestion") }]} />
        {active.map((run) => <div key={run.id}>{cancelRun(run)}</div>)}
      </header>
      {grouping === "record" ? entries.map((entry) => <section key={entry.record_id} className="grid gap-2 border-t border-border-subtle pt-3">
        {link(entry.record_model, entry.record_id)}{entry.decisions.map((decision) => card(decision, entry.record_id, true))}
        {heldRuns(entry)}
      </section>) : <>{[...questions].map(([question, items]) => <section key={question} className="grid gap-2 border-t border-border-subtle pt-3">
        <h3 className={textRoleVariants({ role: "title" })}>{[...items.values()][0]?.decision.kind_label} <CountBadge value={items.size} tone="warning" /></h3>
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
  if (drillIn) {
    return <div className="flex min-h-0 flex-1 flex-col">
      {single ? <PaneToolbar
        scope={<RunStatus run={single} compact />}
        actions={<>{link("workflows.WorkflowRun", single.id, t("timeline.openRun"))}{single.can_cancel ? cancelRun(single) : null}</>}
      /> : <PaneToolbar />}
      <div className="grid min-w-0 gap-4 p-3">
        {single?.status && optionToken(single.status) === "failed" && single.failure_reason ? <Alert tone="danger">{single.failure_reason}</Alert> : null}
        {runs.map((run) => <section key={run.id} className="grid min-w-0 gap-3" aria-label={run.display_name}>
          <RunSteps run={run} recordId={entry?.record_id ?? ""}
            card={(decision, id, compact, records) => card(decision, id, compact, records, run.can_cancel ? run.id : undefined)}
            link={link} retry={retryStep} />
        </section>)}
        {!runs.length ? <InlineEmpty label={t("timeline.empty")} /> : null}
      </div>
    </div>;
  }
  return <div className="grid min-w-0 gap-4">
    <header className="grid min-w-0 gap-1.5"><div className="flex min-w-0 items-center gap-2">
      <h2 className={textRoleVariants({ role: "title", truncate: true, className: "min-w-0" })}>{single?.version?.workflow?.display_name ?? t("timeline.workflows", { count: runs.length })}</h2>
      <CountBadge tone={count ? "warning" : "neutral"} value={count} title={t("timeline.decisions", { count })} />
    </div>{single ? <div className="flex flex-wrap items-center gap-2"><RunStatus run={single} />
      <time dateTime={single.created_at}>{formatDateTime(single.created_at)}</time>
      {link("workflows.WorkflowRun", single.id, t("timeline.openRun"))}</div> : <p className={textRoleVariants({ role: "meta" })}>{t("timeline.history")}</p>}
      {active.map((run) => <div key={run.id}>{cancelRun(run)}</div>)}
    </header>
    {runs.map((run) => <section key={run.id} className="grid min-w-0 gap-3" aria-label={run.display_name}>
      {!single ? <div className="grid gap-1 border-t border-border-subtle pt-3"><h3 className={textRoleVariants({ role: "title" })}>{run.version?.workflow?.display_name}</h3>
        <RunStatus run={run} /><time dateTime={run.created_at}>{formatDateTime(run.created_at)}</time>
        {link("workflows.WorkflowRun", run.id, t("timeline.openRun"))}</div> : null}
      <RunSteps run={run} recordId={entry?.record_id ?? ""}
        card={(decision, id, compact, records) => card(decision, id, compact, records, run.can_cancel ? run.id : undefined)}
        link={link} retry={retryStep} />
    </section>)}
    {entry?.decisions.filter((decision) => !inRuns.has(decision.id)).map((decision) => card(decision, entry.record_id))}
    {!runs.length && !entry?.decisions.length ? <InlineEmpty label={t("timeline.empty")} /> : null}
  </div>;
}

/** The run's state word, derived from its status and the holds its steps carry; the composed
 * status vocabulary (workflows contributes the run-state words) decides the tone. */
const UNKNOWN_STATE_TONE = { unknown: "neutral" } as const;
function RunStatus({ run, compact = false }: { run: TimelineRun; compact?: boolean }) {
  const t = useWorkflowsT();
  const holds = runSteps(run).flatMap((step) => step.hold ? [step.hold] : []);
  const status = optionToken(run.status);
  const state = status === "canceled" || status === "interrupted" ? "stopped" : status === "failed" ? "error"
    : status === "succeeded" ? "plan_complete" : status === "running" ? "plan_running"
    : status === "waiting" ? holds.includes("error") ? "error" : holds.includes("run") ? "run"
      : holds.includes("decision") ? "decision" : "waiting" : "unknown";
  const badge = <StatusBadge value={state} tone={UNKNOWN_STATE_TONE}>{state === "plan_complete" && run.outcome_label || t(`timeline.status.${state}`)}</StatusBadge>;
  if (compact) return badge;
  return <div className="grid gap-1">{badge}
    {status === "failed" && run.failure_reason ? <Alert tone="danger">{run.failure_reason}</Alert> : null}</div>;
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

/** The retained-note renderer shared by RecordTimeline and Activity entries. */
export function TimelineNotes({ notes }: { notes: readonly TimelineNote[] }): ReactElement | null {
  if (!notes.length) return null;
  return <RecordIssues items={notes.map((note, index) => {
    const tone = v.safeParse(NoteTone, note.tone);
    return { id: String(index), ...note, tone: tone.success ? tone.output : "info" };
  })} />;
}

function RunSteps({ run, recordId, card, link, retry }: {
  run: TimelineRun; recordId: string;
  card: (decision: TimelineData[number]["decisions"][number], id: string, compact?: boolean, records?: readonly string[]) => ReactElement;
  link: (model: string, id: string, label?: string) => ReactElement;
  retry: (step: TimelineStep | MappedStep) => ReactElement | null;
}) {
  const t = useWorkflowsT();
  const resolveTone = useStatusTone();
  const originOptions = useEnumOptions(RUN_MODEL, "origin");
  const [expanded, setExpanded] = useState(false);
  const ordered = [...run.graph.nodes].filter((node) => !node.key.endsWith(".body")).sort((a, b) => a.rank - b.rank);
  const done = ordered.filter((node) => node.plan === "done");
  const current = ordered.filter((node) => node.plan === "current");
  const routine = (node: typeof ordered[number]) => !node.step_run?.decision && !node.step_run?.notes.length
    && optionToken(node.step_run?.status) === "succeeded";
  const items: StepListItem[] = [{ id: "trigger", state: "trigger", title: run.trigger_event?.trigger?.display_name
    ?? (run.parent_step ? t("timeline.continuedBy", { name: run.run_as?.display_name ?? t("timeline.user") })
      : optionToken(run.origin) === "manual" ? t("timeline.startedBy", { name: run.run_as?.display_name ?? t("timeline.user") }) : t("timeline.originStarted", { origin: String(optionLabel(originOptions, run.origin)) })), timestamp: run.created_at,
    children: run.trigger_event?.record_model && run.trigger_event.record_id ? link(run.trigger_event.record_model, run.trigger_event.record_id)
      : run.subject_model && run.subject_id ? link(run.subject_model, run.subject_id) : null }];
  const folded = !expanded && runSteps(run).some((step) => optionToken(step.status) === "waiting") && done.length > 6;
  let group: typeof ordered = [];
  const details = (step: TimelineStep | MappedStep | null, title?: string | null) => {
    const records = [...new Map((step?.records ?? []).map((record) => [`${record.record_model}:${record.record_id}`, record])).values()];
    const notes = step?.notes.filter((note) => note.message !== title) ?? [];
    return <>
      <TimelineNotes notes={notes} />
      {records.length ? <div className="flex min-w-0 flex-wrap gap-1">{records.map((record) => record.record_model && record.record_id ? <span key={record.id}>{link(record.record_model, record.record_id, record.label || undefined)}</span> : null)}</div> : null}
      {step?.child_runs.map((child) => <div key={child.id}>{link("workflows.WorkflowRun", child.id, child.display_name)}</div>)}
      {step?.hold === "run" && step.awaited_run ? <Alert tone="warning" title={t("timeline.waitingFor", { name: step.awaited_run.display_name })}>{link("workflows.WorkflowRun", step.awaited_run.id, t("timeline.openRun"))}</Alert> : null}
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
        title: t("timeline.mappedStep", { label: mapped.node_label, index: mapped.map_index + 1 }), outcome: mapped.outcome_label, timestamp: mapped.updated_at,
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
  if (run.stopped_at || ["canceled", "interrupted"].includes(optionToken(run.status))) items.push({ id: "stopped", state: "stopped", title: t("timeline.status.stopped"), timestamp: run.stopped_at ?? run.finished_at,
    detail: t("timeline.stoppedDetail") });
  return <StepList items={items} />;
}
