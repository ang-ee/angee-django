import { defineBaseAddon } from "@angee/app";
import { History } from "lucide-react";
import type { ActivityEntrySource } from "@angee/ui";

import { FieldProvenanceMarks } from "./FieldProvenanceMarks";
import { RecordHistoryTimeline, useRecordHistoryEntries } from "./RecordHistory";
import { enProvenanceMessages } from "./i18n";

/**
 * Provenance contributes field-statement marks to saved forms and one detailed
 * change source to the framework Activity stream. The source itself declines
 * resources without native history.
 */
const provenance = defineBaseAddon({
  id: "provenance",
  icons: { history: History },
  i18n: { provenance: enProvenanceMessages },
  containers: {
    "form#chrome": {
      "provenance.field-provenance": {
        sequence: 10,
        content: <FieldProvenanceMarks />,
      },
    },
    // Spike: the history drill-in the timeline rail selects.
    "record#aside": {
      "provenance.history": {
        sequence: 13,
        content: {
          label: "History", icon: "history", panelClassName: "p-0",
          when: (context) => context.view.kind === "record" && Boolean(context.view.sqid) && Boolean(context.route?.history),
          render: (context) => (
            <RecordHistoryTimeline model={context.route?.canonicalLabel ?? context.route?.modelLabel ?? ""} id={context.view.sqid ?? ""} />
          ),
        },
      },
    },
    "record#activity": {
      "provenance.history": {
        content: { kinds: ["change", "source-event"], useEntries: useRecordHistoryEntries } satisfies ActivityEntrySource,
      },
    },
  },
});

export {
  HistoryEntrySummary,
  RecordHistoryTimeline,
  useRecordHistory,
  useRecordHistoryEntries,
} from "./RecordHistory";

export default provenance;
