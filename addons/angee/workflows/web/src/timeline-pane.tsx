import { useCallback, useEffect, useRef } from "react";
import { useDecisionFieldMarks } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { TIMELINE_TAB_ID } from "@angee/timeline";
import { useChatter } from "@angee/ui";
import type { RecordTimelineProps, TimelineRecord } from "./RecordTimeline";
import { RecordTimelineAttentionDocument, RUN_MODELS } from "./documents.console";

const NO_RECORDS: Parameters<typeof useDecisionFieldMarks>[0] = [];

export function useRecordTimelineAttentionQuery(record: RecordTimelineProps["record"], enabled = true) {
  const records = Array.isArray(record) ? record : [record as TimelineRecord];
  return useAuthoredQuery(RecordTimelineAttentionDocument, { records }, {
    models: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"], records,
    relatedModels: [...RUN_MODELS, "decisions.Decision"], enabled: enabled && records.length > 0 && records.every((item) => Boolean(item.id)),
  });
}

/** One eager owner publishes marks and reacts to new question identities by revealing the Timeline. */
export function useRecordTimelineAttention(record: RecordTimelineProps["record"]) {
  const chatter = useChatter();
  const query = useRecordTimelineAttentionQuery(record);
  const reveal = useCallback(() => {
    chatter.setActiveTab(TIMELINE_TAB_ID); chatter.setCollapsed(false);
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
    if (previous.current?.key !== key) {
      if (hasRuns || current.size) chatter.setInitialActiveTab(TIMELINE_TAB_ID);
      if (current.size) chatter.setCollapsed(false);
    } else if ([...current].some((id) => !previous.current!.ids.has(id))) {
      chatter.setInitialActiveTab(TIMELINE_TAB_ID, true); chatter.setCollapsed(false);
    }
    previous.current = { key, ids: current };
  }, [hasRuns, key, ids, chatter.setInitialActiveTab, chatter.setCollapsed]);
  return count;
}
