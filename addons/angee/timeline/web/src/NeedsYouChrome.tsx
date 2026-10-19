import { useAuthoredQuery } from "@angee/refine";
import { PaneToolbarIconButton, useChatter, useRecordChromeContext } from "@angee/ui";
import * as React from "react";

import { TimelineAttentionDocument } from "./documents.console";
import { TIMELINE_FOCUS_NEEDS_ME, TIMELINE_TAB_ID } from "./RecordTimelinePane";

/**
 * The record-header Needs you control: how many lines on this record wait on
 * the viewer, from the ledger. It shows only while something waits and opens
 * the Timeline on the needs-me scope; the lines carry the verbs.
 */
export function NeedsYouChrome(): React.ReactElement | null {
  const record = useRecordChromeContext();
  const { setActiveTab, setCollapsed } = useChatter();
  const model = record.canonicalResource;
  const variables = React.useMemo(() => ({ model, id: record.recordId }), [model, record.recordId]);
  const query = useAuthoredQuery(TimelineAttentionDocument, variables, {
    enabled: Boolean(model && record.recordId),
    models: ["timeline.Event"],
    records: [{ model, id: record.recordId }],
  });
  const count = query.data?.timeline_attention ?? 0;
  if (!count) return null;
  return (
    <PaneToolbarIconButton
      icon="triangle-alert"
      label={`Needs you · ${count}`}
      badge={count}
      onClick={() => {
        setCollapsed(false);
        setActiveTab(TIMELINE_TAB_ID, { from: TIMELINE_TAB_ID, focus: TIMELINE_FOCUS_NEEDS_ME });
      }}
    />
  );
}
