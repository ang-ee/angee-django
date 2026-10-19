import { defineBaseAddon } from "@angee/app";
import { ListOrdered } from "lucide-react";

import { NeedsYouChrome } from "./NeedsYouChrome";
import { RecordTimelinePane, TIMELINE_TAB_ID } from "./RecordTimelinePane";

export { TIMELINE_FOCUS_NEEDS_ME, TIMELINE_KINDS_CONTAINER, TIMELINE_TAB_ID, RecordTimelinePane } from "./RecordTimelinePane";

/**
 * The record's one chronology: the Timeline tab, first in the context aside, on
 * every model-backed record. Kinds present themselves through `timeline#kinds`,
 * the container this addon owns; their Python kinds project the ledger the read pages.
 * The Needs you header control counts the lines that wait on the viewer.
 */
export default defineBaseAddon({
  id: "timeline",
  icons: { timeline: ListOrdered },
  containers: {
    "timeline#kinds": {},
    "form#chrome": {
      "timeline.needs-you": { sequence: 10, content: <NeedsYouChrome /> },
    },
    "record#aside": {
      [TIMELINE_TAB_ID]: {
        sequence: 10,
        content: {
          label: "Timeline",
          icon: "timeline",
          panelClassName: "p-0",
          render: (context, selection) => (
            <RecordTimelinePane
              key={`${context.route?.canonicalLabel ?? context.route?.modelLabel}:${context.view.kind === "record" ? context.view.sqid : ""}`}
              context={context}
              selection={selection}
            />
          ),
        },
      },
    },
  },
});
