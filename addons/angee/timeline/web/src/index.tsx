import { defineBaseAddon } from "@angee/app";
import { ListOrdered } from "lucide-react";

import { RecordTimelinePane, TIMELINE_TAB_ID } from "./RecordTimelinePane";

export { TIMELINE_TAB_ID } from "./RecordTimelinePane";

/**
 * The record timeline rail as a `record#aside` tab. Spike: it sits beside the
 * Activity tab so the two can be compared on the same record; the refactor
 * replaces Activity with it.
 */
export default defineBaseAddon({
  id: "timeline",
  icons: { timeline: ListOrdered },
  containers: {
    "record#aside": {
      [TIMELINE_TAB_ID]: {
        sequence: 5,
        content: {
          label: "Timeline",
          icon: "timeline",
          panelClassName: "p-0",
          when: ({ view }) => view.kind === "record" && Boolean(view.sqid),
          render: (context) => (
            <RecordTimelinePane
              key={`${context.route?.canonicalLabel ?? context.route?.modelLabel}:${context.view.sqid}`}
              context={context}
            />
          ),
        },
      },
    },
  },
});

export { RecordTimelinePane } from "./RecordTimelinePane";
