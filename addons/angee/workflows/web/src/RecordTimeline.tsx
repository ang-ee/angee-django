import { useCallback, useMemo, useState, type ReactElement } from "react";
import type { DocumentType } from "@angee/gql/console";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { DecisionCard, decisionFieldMarks } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import {
  Alert, Badge, Button, Chip, CountBadge, ErrorBanner, InlineEmpty, LoadingPanel, RecordActionBar, RecordIssues, RecordReference,
  SegmentedControl, StepList, optionToken, titleCase, useActiveRecordForm, useActionResultMutation, useChatter,
  useRecordFieldMarks, useRecordPeek, formatDateTime, type StepListItem, type Tone,
} from "@angee/ui";
import { RecordTimelineDocument, RUN_MODELS } from "./documents.console";
import { useStepRetryActions } from "./step-retry";

export interface TimelineRecord { model: string; id: string }
export interface RecordTimelineProps { record: TimelineRecord | readonly TimelineRecord[]; recordState?: { label: string; tone: Tone } }
export type TimelineSelection = DocumentType<typeof RecordTimelineDocument>["record_timeline"];
export type TimelineData = TimelineSelection["records"];
type TimelineRun = TimelineData[number]["runs"][number];
type TimelineStep = NonNullable<TimelineRun["graph"]["nodes"][number]["step_run"]>;
type MappedStep = TimelineStep["map_steps"][number];
const EMPTY_TIMELINE: TimelineData = [];
const runSteps = (run: TimelineRun) => run.graph.nodes.flatMap(({ step_run }) => step_run ? [step_run, ...step_run.map_steps] : []);

/** One input: a record or a selection. The contributing surface chooses placement. */
export function RecordTimeline({ record, recordState }: RecordTimelineProps): ReactElement {
  const query = useRecordTimelineQuery(record);
  const [stop] = useActionResultMutation<ActionFieldName>("cancel_workflow_run", {
    dataProviderName: "console", invalidateModels: [...RUN_MODELS, "decisions.Decision"],
  });
  const [retry] = useActionResultMutation<ActionFieldName>("retry_step", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  if (query.isLoading) return <LoadingPanel />;
  if (query.error) return <ErrorBanner description="Timeline unavailable." />;
  return <RecordTimelineView data={query.data?.record_timeline?.records ?? EMPTY_TIMELINE} openCount={query.data?.record_timeline?.open_decision_count ?? 0} set={Array.isArray(record)} recordState={recordState}
    onAnswered={query.refetch} onStop={async (ids) => {
      for (const id of ids) await stop(id);
      await query.refetch();
    }} onRetry={async (id) => { await retry(id); await query.refetch(); }} />;
}

export function useRecordTimelineQuery(record: RecordTimelineProps["record"]) {
  const records = useMemo(() => Array.isArray(record) ? record : [record as TimelineRecord], [record]);
  return useAuthoredQuery(RecordTimelineDocument, { records }, {
    models: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"],
    records, relatedModels: [...RUN_MODELS, "decisions.Decision"],
  });
}

/** Presentation over the same generated read, used by stories and interaction tests. */
export function RecordTimelineView({ data, openCount, set = false, recordState, onAnswered, onStop, onRetry }: {
  data: TimelineData; openCount: number; set?: boolean;
  recordState?: RecordTimelineProps["recordState"];
  onAnswered?: () => void | Promise<unknown>;
  onStop?: (ids: readonly string[]) => void | Promise<unknown>;
  onRetry?: (id: string) => void | Promise<unknown>;
}): ReactElement {
  const [highlighted, highlight] = useState<string>();
  const [grouping, setGrouping] = useState<"record" | "question">("record");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const form = useActiveRecordForm();
  const chatter = useChatter();
  const openRecord = useRecordPeek();
  const retryActions = useStepRetryActions();
  const reveal = useCallback((id: string) => {
    chatter.setActiveTab("workflows.timeline"); chatter.setCollapsed(false); highlight(id);
  }, [chatter.setActiveTab, chatter.setCollapsed]);
  const publications = useMemo(() => data.map((entry) => ({
    model: entry.record_model, id: entry.record_id,
    marks: decisionFieldMarks(entry.decisions, entry.record_id), reveal,
  })), [data, reveal]);
  useRecordFieldMarks(publications);
  const link = (model: string, id: string, label?: string) => <Chip tone="info" size="sm"><RecordReference model={model} id={id} label={label}
    onOpen={() => openRecord({ model, id, label })} /></Chip>;
  const card = (decision: TimelineData[number]["decisions"][number], id: string, compact = false, records?: readonly string[]) =>
    <DecisionCard key={decision.id} decision={decision} selfId={id} highlighted={highlighted === decision.id}
      compact={compact} inStep={records ? { records } : undefined} onAnswered={onAnswered}
      onEditField={form?.id === id ? form.focusField : undefined} />;
  const runs = [...new Map(data.flatMap(({ runs }) => runs).map((run) => [run.id, run])).values()];
  const count = openCount;
  const active = runs.filter((run) => run.can_cancel);
  const held = (run: TimelineRun) => runSteps(run).filter((step) => step.hold && step.hold !== "decision");
  const act = async (action: () => void | Promise<unknown>) => {
    setBusy(true); setError(undefined);
    try { await action(); } catch (error) { setError(error instanceof Error ? error.message : "Action failed."); }
    finally { setBusy(false); }
  };
  const retryStep = (step: TimelineStep | MappedStep) => !step.can_retry ? null
    : step.requires_duplicate_acknowledgement ? <RecordActionBar record={step} actions={retryActions}
      reload={async () => { await onAnswered?.(); return null; }} blocked={busy} />
    : <Button size="sm" disabled={busy} onClick={() => void act(() => onRetry?.(step.id))}>Retry</Button>;
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
      let group = questions.get(decision.kind_label);
      if (!group) { group = new Map(); questions.set(decision.kind_label, group); }
      const item = group.get(decision.id);
      if (item) item.entries.push(entry);
      else group.set(decision.id, { entries: [entry], decision });
    }
    return <div className="grid min-w-0 gap-4">
      <header className="grid gap-2"><h2 className="text-15 font-semibold">Review <CountBadge tone={count ? "warning" : "neutral"} value={count} /></h2>
        <p className="text-13 text-fg-muted">{data.length} record{data.length === 1 ? "" : "s"} · {count} open decision{count === 1 ? "" : "s"} · {runs.filter((run) => held(run).length).length} run{runs.filter((run) => held(run).length).length === 1 ? "" : "s"} waiting or stopped</p>
        <SegmentedControl<"record" | "question"> aria-label="Group by" value={grouping} onValueChange={setGrouping}
          options={[{ value: "record", label: "By record" }, { value: "question", label: "By question" }]} />
        {active.length ? <Button size="sm" variant="secondary" className="w-fit" disabled={busy}
          onClick={() => void act(() => onStop?.(active.map(({ id }) => id)))}>Stop and do it manually</Button> : null}
      </header>
      {grouping === "record" ? entries.map((entry) => <section key={entry.record_id} className="grid gap-2 border-t border-border-subtle pt-3">
        {link(entry.record_model, entry.record_id)}{entry.decisions.map((decision) => card(decision, entry.record_id, true))}
        {heldRuns(entry)}
      </section>) : <>{[...questions].map(([question, items]) => <section key={question} className="grid gap-2 border-t border-border-subtle pt-3">
        <h3 className="text-13 font-semibold">{question} <CountBadge value={items.size} tone="warning" /></h3>
        {[...items.values()].map(({ entries, decision }) => <div key={decision.id} className="grid gap-1">
          <div className="flex flex-wrap gap-1">{entries.map((entry) => <span key={entry.record_id}>{link(entry.record_model, entry.record_id)}</span>)}</div>
          {card(decision, entries[0]!.record_id, true)}
        </div>)}
      </section>)}{entries.filter((entry) => entry.runs.some((run) => held(run).length)).map((entry) =>
        <section key={entry.record_id} className="grid gap-2 border-t border-border-subtle pt-3">
          {link(entry.record_model, entry.record_id)}{heldRuns(entry)}
        </section>)}</>}
      {!entries.length ? <InlineEmpty label="No visible open decisions or held runs." /> : null}
      {error ? <ErrorBanner description={error} /> : null}
    </div>;
  }
  const entry = data[0];
  const single = runs.length === 1 ? runs[0] : undefined;
  const inRuns = new Set(runs.flatMap((run) => runSteps(run).flatMap((step) => step.decision ? [step.decision.id] : [])));
  return <div className="grid min-w-0 gap-4">
    <header className="grid min-w-0 gap-1.5"><div className="flex min-w-0 items-center gap-2">
      <h2 className="min-w-0 truncate text-15 font-semibold">{single?.version?.workflow?.display_name ?? `${runs.length} workflows`}</h2>
      <CountBadge tone={count ? "warning" : "neutral"} value={count} title={`${count} open decision${count === 1 ? "" : "s"}`} />
    </div>{single ? <div className="flex flex-wrap items-center gap-2"><RunStatus run={single} recordState={recordState} />
      <time dateTime={single.created_at}>{formatDateTime(new Date(single.created_at))}</time>
      {link("workflows.WorkflowRun", single.id, "Open run")}</div> : <p className="text-13 text-fg-muted">Runs that worked on this record, oldest first.</p>}
      {active.length ? <Button size="sm" variant="secondary" className="mt-1 w-fit" disabled={busy}
        onClick={() => void act(() => onStop?.(active.map(({ id }) => id)))}>Stop and do it manually</Button> : null}
    </header>
    {runs.map((run) => <section key={run.id} className="grid min-w-0 gap-3" aria-label={run.display_name}>
      {!single ? <div className="grid gap-1 border-t border-border-subtle pt-3"><h3 className="text-13 font-semibold">{run.version?.workflow?.display_name}</h3>
        <RunStatus run={run} recordState={recordState} /><time dateTime={run.created_at}>{formatDateTime(new Date(run.created_at))}</time>
        {link("workflows.WorkflowRun", run.id, "Open run")}</div> : null}
      <RunSteps run={run} recordId={entry?.record_id ?? ""} card={card} link={link} retry={retryStep} />
    </section>)}
    {entry?.decisions.filter((decision) => !inRuns.has(decision.id)).map((decision) => card(decision, entry.record_id))}
    {!runs.length && !entry?.decisions.length ? <InlineEmpty label="No visible workflow history or decisions." /> : null}
    {error ? <ErrorBanner description={error} /> : null}
  </div>;
}

function RunStatus({ run, recordState }: { run: TimelineRun; recordState?: RecordTimelineProps["recordState"] }) {
  const holds = runSteps(run).flatMap((step) => step.hold ? [step.hold] : []);
  const status = optionToken(run.status);
  if (recordState && status === "succeeded") return <Badge tone={recordState.tone}>{recordState.label}</Badge>;
  const label = run.stopped_at || status === "canceled" ? "Stopped" : holds.includes("error") ? "Stopped on an error"
    : holds.includes("run") ? "Waiting for another run" : holds.includes("decision") ? "Waiting for decisions"
    : status === "succeeded" ? run.outcome_label || "Plan complete" : "Running";
  const tone: Tone = holds.includes("error") ? "danger" : holds.length ? "warning" : status === "succeeded" ? "success" : "neutral";
  return <Badge tone={tone}>{label}</Badge>;
}

function RunSteps({ run, recordId, card, link, retry }: {
  run: TimelineRun; recordId: string;
  card: (decision: TimelineData[number]["decisions"][number], id: string, compact?: boolean, records?: readonly string[]) => ReactElement;
  link: (model: string, id: string, label?: string) => ReactElement;
  retry: (step: TimelineStep | MappedStep) => ReactElement | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const ordered = [...run.graph.nodes].filter((node) => !node.key.endsWith(".body")).sort((a, b) => a.rank - b.rank);
  const done = ordered.filter((node) => node.plan === "done");
  const current = ordered.filter((node) => node.plan === "current");
  const routine = (node: typeof ordered[number]) => !node.step_run?.decision && !node.step_run?.notes.length
    && optionToken(node.step_run?.status) === "succeeded";
  const items: StepListItem[] = [{ id: "trigger", state: "trigger", title: run.trigger_event?.trigger?.display_name
    ?? (optionToken(run.origin) === "manual" ? `${run.start_label ?? "Started manually"} by ${run.run_as?.display_name ?? "a user"}` : `${titleCase(run.origin)} started`), timestamp: run.created_at,
    children: run.trigger_event?.record_model && run.trigger_event.record_id ? link(run.trigger_event.record_model, run.trigger_event.record_id)
      : run.subject_model && run.subject_id ? link(run.subject_model, run.subject_id) : null }];
  const folded = !expanded && runSteps(run).some((step) => optionToken(step.status) === "waiting") && done.length > 6;
  let group: typeof ordered = [];
  const details = (step: TimelineStep | MappedStep | null, title?: string | null) => {
    const records = [...new Map((step?.records ?? []).map((record) => [`${record.record_model}:${record.record_id}`, record])).values()];
    const notes = step?.notes.filter((note) => note.message !== title) ?? [];
    return <>
      {notes.length ? <RecordIssues items={notes.map((note, index) => ({ id: String(index), ...note, tone: note.tone as "info" | "success" | "warning" | "danger" }))} /> : null}
      {records.length ? <div className="flex min-w-0 flex-wrap gap-1">{records.map((record) => record.record_model && record.record_id ? <span key={record.id}>{link(record.record_model, record.record_id, record.label || undefined)}</span> : null)}</div> : null}
      {step?.child_runs.map((child) => <div key={child.id}>{link("workflows.WorkflowRun", child.id, child.display_name)}</div>)}
      {step?.hold === "run" && step.awaited_run ? <p className="text-13 font-medium text-warning-text">Waiting for {link("workflows.WorkflowRun", step.awaited_run.id, step.awaited_run.display_name)}</p> : null}
      {step?.hold === "error" ? <Alert tone="danger" title="Stopped on an error" actions={retry(step)}>{step.failure_reason || step.wait_reason}</Alert> : null}
      {step?.decision ? card(step.decision, recordId, false, records.flatMap((record) => record.record_id ? [record.record_id] : [])) : null}
    </>;
  };
  const flush = () => {
    if (!group.length) return;
    if (group.length > 1) items.push({ id: `fold-${group[0]!.key}`, state: "done", title: <Button variant="link" size="sm" className="h-auto px-0" onClick={() => setExpanded(true)}>Show {group.length} steps done</Button>, detail: group.map(({ label }) => label).join(", ") });
    else append(group[0]!);
    group = [];
  };
  const append = (node: typeof ordered[number]) => {
    const step = node.step_run;
    const outcome = step?.outcome_label && step.outcome_label !== titleCase(step.outcome) ? step.outcome_label : null;
    const title = optionToken(step?.status) === "succeeded"
      ? step?.notes.find((note) => ["info", "success"].includes(note.tone))?.message || outcome || node.label : node.label;
    items.push({ id: node.key, state: optionToken(step?.status) === "canceled" ? "stopped" : node.plan === "current" ? "current" : "done", title,
      timestamp: step?.updated_at, tone: step?.hold === "error" ? "danger" : "success",
      children: details(step, title) });
    for (const mapped of step?.map_steps ?? []) {
      const status = optionToken(mapped.status);
      items.push({ id: mapped.id, state: mapped.hold || ["running", "waiting"].includes(status) ? "current"
        : status === "canceled" ? "stopped" : status === "ready" ? "planned" : "done",
        title: `${mapped.node_label} · ${mapped.map_index + 1}`, outcome: mapped.outcome_label, timestamp: mapped.updated_at,
        tone: mapped.hold === "error" ? "danger" : "success", children: details(mapped) });
    }
  };
  for (const node of done) {
    if (folded && routine(node)) group.push(node);
    else { flush(); append(node); }
  }
  flush(); current.forEach(append);
  for (const node of ordered.filter((node) => node.plan === "certain")) items.push({ id: node.key, state: "planned", title: node.label });
  const optional = ordered.filter((node) => node.plan === "optional");
  if (optional.length) items.push({ id: "optional", state: "optional", title: `May also: ${optional.map(({ label }) => label).join(", ")}` });
  if (run.stopped_at || optionToken(run.status) === "canceled") items.push({ id: "stopped", state: "stopped", title: "Stopped", timestamp: run.stopped_at ?? run.finished_at,
    detail: "The run ended and its open decisions were withdrawn. The record stays as it is, to finish by hand." });
  return <StepList items={items} />;
}
