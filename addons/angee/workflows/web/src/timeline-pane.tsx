import { useCallback, useEffect, useMemo, useRef } from "react";
import { useDecisionFieldMarks } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { ScrollArea, useChatter, useChatterContent, usePrimaryPane, useRecordPeekContext } from "@angee/ui";
import { RecordTimeline, type RecordTimelineProps, type TimelineRecord } from "./RecordTimeline";
import { RecordTimelineAttentionDocument, RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

const NO_RECORDS: Parameters<typeof useDecisionFieldMarks>[0] = [];

export function useRecordTimelineAttentionQuery(record: RecordTimelineProps["record"], enabled = true) {
  const records = useMemo(() => Array.isArray(record) ? record : [record as TimelineRecord], [record]);
  return useAuthoredQuery(RecordTimelineAttentionDocument, { records }, {
    models: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"], records,
    relatedModels: [...RUN_MODELS, "decisions.Decision"], enabled: enabled && records.length > 0 && records.every((item) => Boolean(item.id)),
  });
}

/** Publish the same timeline through the console's existing pane hosts. */
export function useRecordTimelinePane({ record, side }: RecordTimelineProps & { side: "left" | "right" }) {
  const passive = Boolean(useRecordPeekContext());
  const t = useWorkflowsT();
  const pane = useMemo(() => <RecordTimeline record={record} />, [record]);
  const query = useRecordTimelineAttentionQuery(record, !passive);
  const count = query.data?.record_timeline.open_decision_count ?? 0;
  const right = useMemo(() => !passive && side === "right" ? { tabs: [{ id: "workflows.timeline", label: t("timeline.title"), count, children: pane }] } : null, [passive, side, pane, count, t]);
  const left = useMemo(() => !passive && side === "left" ? <ScrollArea className="h-full" viewportClassName="overflow-x-hidden p-4">{pane}</ScrollArea> : null, [passive, side, pane]);
  useChatterContent(right);
  usePrimaryPane(left);
  return count;
}

/** One eager owner publishes marks and reacts to new question identities. */
export function useRecordTimelineAttention(record: RecordTimelineProps["record"], side = "right") {
  const chatter = useChatter();
  const query = useRecordTimelineAttentionQuery(record);
  const reveal = useCallback(() => {
    chatter.setActiveTab("workflows.timeline"); chatter.setCollapsed(false);
  }, [chatter.setActiveTab, chatter.setCollapsed]);
  const entries = query.data?.record_timeline.records ?? NO_RECORDS;
  useDecisionFieldMarks(entries, reveal);
  const key = (Array.isArray(record) ? record : [record as TimelineRecord]).map((item) => `${item.model}:${item.id}`).sort().join("|");
  const ids = entries.flatMap((entry) => entry.decisions.filter((decision) => decision.is_open).map((decision) => decision.id)).sort().join("|");
  const previous = useRef<{ key: string; ids: Set<string> } | undefined>(undefined);
  const hasRuns = query.data?.record_timeline.has_runs;
  const count = query.data?.record_timeline?.open_decision_count ?? 0;
  useEffect(() => {
    if (hasRuns === undefined) return;
    const current = new Set(ids.split("|").filter(Boolean));
    if (side === "right") {
      if (previous.current?.key !== key) {
        if (hasRuns || current.size) chatter.setInitialActiveTab("workflows.timeline");
        if (current.size) chatter.setCollapsed(false);
      } else if ([...current].some((id) => !previous.current!.ids.has(id))) {
        chatter.setInitialActiveTab("workflows.timeline", true); chatter.setCollapsed(false);
      }
    }
    previous.current = { key, ids: current };
  }, [side, hasRuns, key, ids, chatter.setInitialActiveTab, chatter.setCollapsed]);
  return count;
}
