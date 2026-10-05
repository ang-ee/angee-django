import { useCallback, useEffect, useMemo } from "react";
import { useDecisionFieldMarks } from "@angee/decisions";
import { ScrollArea, useChatter, useChatterContent, usePrimaryPane } from "@angee/ui";
import { RecordTimeline, useRecordTimelineQuery, type RecordTimelineProps } from "./RecordTimeline";

const NO_RECORDS: NonNullable<ReturnType<typeof useRecordTimelineQuery>["data"]>["record_timeline"]["records"] = [];

/** Publish the same timeline through the console's existing pane hosts. */
export function useRecordTimelinePane({ record, recordState, side }: RecordTimelineProps & { side: "left" | "right" }) {
  const pane = useMemo(() => <RecordTimeline record={record} recordState={recordState} />, [record, recordState]);
  const count = useRecordTimelineAttention(record, side);
  const right = useMemo(() => side === "right" ? { tabs: [{ id: "workflows.timeline", label: "Timeline", count, children: pane }] } : null, [side, pane, count]);
  const left = useMemo(() => side === "left" ? <ScrollArea className="h-full" viewportClassName="overflow-x-hidden p-4">{pane}</ScrollArea> : null, [side, pane]);
  useChatterContent(right);
  usePrimaryPane(left);
  return count;
}

/** The eager tab count and page-published pane share attention and initial placement. */
export function useRecordTimelineAttention(record: RecordTimelineProps["record"], side = "right") {
  const chatter = useChatter();
  const query = useRecordTimelineQuery(record);
  const reveal = useCallback(() => {
    chatter.setActiveTab("workflows.timeline"); chatter.setCollapsed(false);
  }, [chatter.setActiveTab, chatter.setCollapsed]);
  useDecisionFieldMarks(query.data?.record_timeline.records ?? NO_RECORDS, reveal);
  const hasRuns = query.data?.record_timeline?.records.some((entry) => entry.runs.length > 0);
  const count = query.data?.record_timeline?.open_decision_count ?? 0;
  useEffect(() => {
    if (side === "right" && hasRuns !== undefined) {
      if (count) {
        chatter.setInitialActiveTab("workflows.timeline", true);
        chatter.setCollapsed(false);
      } else chatter.setInitialActiveTab(hasRuns ? "workflows.timeline" : "messaging.comments");
    }
  }, [side, hasRuns, count, chatter.setInitialActiveTab, chatter.setCollapsed]);
  return count;
}
