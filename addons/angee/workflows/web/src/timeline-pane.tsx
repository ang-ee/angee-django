import { useCallback, useEffect, useMemo, useRef } from "react";
import { useDecisionFieldMarks } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { ACTIVITY_TAB_ID, ScrollArea, useChatter, usePrimaryPane, useRecordPeekContext } from "@angee/ui";
import { RecordTimeline, type RecordTimelineProps, type TimelineRecord } from "./RecordTimeline";
import { RecordTimelineAttentionDocument, RUN_MODELS } from "./documents.console";

const NO_RECORDS: Parameters<typeof useDecisionFieldMarks>[0] = [];

export function useRecordTimelineAttentionQuery(record: RecordTimelineProps["record"], enabled = true) {
  const records = useMemo(() => Array.isArray(record) ? record : [record as TimelineRecord], [record]);
  return useAuthoredQuery(RecordTimelineAttentionDocument, { records }, {
    models: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"], records,
    relatedModels: [...RUN_MODELS, "decisions.Decision"], enabled: enabled && records.length > 0 && records.every((item) => Boolean(item.id)),
  });
}

/** Publish the timeline through the left-pane host; record Activity owns the aside. */
export function useRecordTimelinePane({ record }: RecordTimelineProps) {
  const passive = Boolean(useRecordPeekContext());
  const pane = useMemo(() => <RecordTimeline record={record} />, [record]);
  const left = useMemo(() => !passive ? <ScrollArea className="h-full" viewportClassName="overflow-x-hidden p-4">{pane}</ScrollArea> : null, [passive, pane]);
  usePrimaryPane(left);
}

/** One eager owner publishes marks and reacts to new question identities. */
export function useRecordTimelineAttention(record: RecordTimelineProps["record"], side = "right") {
  const chatter = useChatter();
  const query = useRecordTimelineAttentionQuery(record);
  const reveal = useCallback(() => {
    chatter.setActiveTab(ACTIVITY_TAB_ID); chatter.setCollapsed(false);
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
        if (hasRuns || current.size) chatter.setInitialActiveTab(ACTIVITY_TAB_ID);
        if (current.size) chatter.setCollapsed(false);
      } else if ([...current].some((id) => !previous.current!.ids.has(id))) {
        chatter.setInitialActiveTab(ACTIVITY_TAB_ID, true); chatter.setCollapsed(false);
      }
    }
    previous.current = { key, ids: current };
  }, [side, hasRuns, key, ids, chatter.setInitialActiveTab, chatter.setCollapsed]);
  return count;
}
