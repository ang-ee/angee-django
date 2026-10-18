import { useDecision } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { useMemo, type ReactNode } from "react";
import { ErrorBanner, MetaSection, NavLink, RecordReference, useRouteHref, type ActivityEntry, type ActivityEntrySource, type ActivityRecord, type ActivityWindow, type ContainerChild } from "@angee/ui";

import { DecisionWaitingRunsDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";

import { RecordTimelineDecisionCard, TimelineNotes, useRecordTimelineQuery } from "./RecordTimeline";
import { useRecordTimelineAttention } from "./timeline-pane";

export function DecisionRunOrigin() {
  const { decision } = useDecision();
  const t = useWorkflowsT();
  const href = useRouteHref();
  const decisionId = decision.id;
  const query = useAuthoredQuery(DecisionWaitingRunsDocument, { decision: decisionId }, {
    models: ["decisions.Decision", "workflows.StepRun"],
    records: [{ model: "decisions.Decision", id: decisionId }],
    relatedModels: ["workflows.StepRun"],
  });
  if (query.isLoading) return null;
  if (query.error) return <ErrorBanner description={t("catalogue.originUnavailable")} />;
  const steps = query.data?.steprun.flatMap((step) => step.run ? [{ ...step, run: step.run }] : []) ?? [];
  if (!steps.length) return null;
  return <MetaSection headingLevel={2} title={t("catalogue.waitingRuns")}><ul className="space-y-2">{steps.map((step) =>
    <li key={step.id}><NavLink href={href("workflows.runs.record", { id: step.run.id })} variant="inline">
      {step.run.display_name}</NavLink>{" · "}{step.node_label}{step.is_mapped ? ` [${step.map_index}]` : ""}</li>)}</ul></MetaSection>;
}

/** Workflow lifecycle, notes and waiting decisions join the framework Activity stream. */
export const workflowActivitySource: ContainerChild<ActivityEntrySource> = {
  sequence: 30,
  content: {
    kinds: ["run", "decision"],
    useEntries: useWorkflowActivityEntries,
  },
};

/** The run a decision waits in, as a `decisions#origin` child. */
export const decisionRunOrigin: ContainerChild<ReactNode> = { content: <DecisionRunOrigin /> };

function useWorkflowActivityEntries(record: ActivityRecord, window: ActivityWindow): readonly ActivityEntry[] {
  const t = useWorkflowsT();
  const timelineRecord = useMemo(() => ({ model: record.model, id: record.id }), [record.id, record.model]);
  const query = useRecordTimelineQuery(timelineRecord, Boolean(record.model && record.id), window.limit);
  useRecordTimelineAttention(timelineRecord);
  return useMemo(() => {
    const selection = query.data?.record_timeline;
    const entry = selection?.records[0];
    if (!entry) return [];
    const activities: ActivityEntry[] = [];
    const decisionContexts = new Map<string, { reference: ReactNode; records: readonly string[] }>();
    for (const run of entry.runs) {
      const actor = run.run_as?.display_name ?? t("timeline.user");
      const reference = <RecordReference model="workflows.WorkflowRun" id={run.id} label={run.display_name} />;
      activities.push({
        at: run.created_at,
        kind: "run",
        actor,
        title: t("activity.runStarted", { name: run.display_name }),
        body: null,
        reference,
      });
      for (const node of run.graph.nodes) {
        const step = node.step_run;
        if (!step) continue;
        if (step.decision) decisionContexts.set(step.decision.id, {
          reference,
          records: step.records.flatMap((item) => item.record_id ? [item.record_id] : []),
        });
        for (const note of step.notes) activities.push({
          at: step.updated_at,
          kind: "run",
          actor,
          title: t("activity.stepNote", { name: node.label }),
          body: <TimelineNotes notes={[note]} />,
          reference,
        });
        for (const mapped of step.map_steps) {
          if (mapped.decision) decisionContexts.set(mapped.decision.id, {
            reference,
            records: mapped.records.flatMap((item) => item.record_id ? [item.record_id] : []),
          });
          for (const note of mapped.notes) activities.push({
            at: mapped.updated_at,
            kind: "run",
            actor,
            title: t("activity.stepNote", { name: `${mapped.node_label} · ${mapped.map_index + 1}` }),
            body: <TimelineNotes notes={[note]} />,
            reference,
          });
        }
      }
      const finishedAt = run.stopped_at ?? run.finished_at;
      if (finishedAt) activities.push({
        at: finishedAt,
        kind: "run",
        actor,
        title: t("activity.runFinished", { name: run.display_name }),
        body: run.outcome_label || run.failure_reason || null,
        reference,
      });
    }
    for (const decision of entry.decisions) {
      const context = decisionContexts.get(decision.id);
      activities.push({
        at: decision.created_at,
        kind: "decision",
        actor: decision.requester?.display_name ?? t("timeline.user"),
        title: decision.kind_label,
        body: <RecordTimelineDecisionCard decision={decision} recordId={entry.record_id}
          records={context?.records} />,
        reference: context?.reference ?? null,
      });
    }
    return activities.sort((left, right) => Date.parse(String(right.at)) - Date.parse(String(left.at))).slice(0, window.limit);
  }, [query.data?.record_timeline, t, window.limit]);
}
