import { useEffect, useMemo } from "react";
import { ScrollArea, useChatter, useChatterContent, usePrimaryPane } from "@angee/ui";
import { RecordTimeline, useRecordTimelineQuery, type RecordTimelineProps } from "./RecordTimeline";

/** Publish the same timeline through the console's existing pane hosts. */
export function useRecordTimelinePane({ record, side }: RecordTimelineProps & { side: "left" | "right" }) {
  const pane = useMemo(() => <RecordTimeline record={record} />, [record]);
  const chatter = useChatter();
  const query = useRecordTimelineQuery(record);
  const hasRuns = query.data?.record_timeline?.records.some((entry) => entry.runs.length > 0);
  const right = useMemo(() => side === "right" ? { tabs: [{ id: "workflows.timeline", label: "Timeline", children: pane }] } : null, [side, pane]);
  const left = useMemo(() => side === "left" ? <ScrollArea className="h-full" viewportClassName="overflow-x-hidden p-4">{pane}</ScrollArea> : null, [side, pane]);
  useChatterContent(right);
  usePrimaryPane(left);
  useEffect(() => {
    if (side === "right" && hasRuns !== undefined) {
      chatter.setInitialActiveTab(hasRuns ? "workflows.timeline" : "chatter.comments");
    }
  }, [side, hasRuns, chatter.setInitialActiveTab]);
}
