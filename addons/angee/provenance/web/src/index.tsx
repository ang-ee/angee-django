import { defineBaseAddon } from "@angee/app";
import type { ActivityEntrySource } from "@angee/ui";

import { FieldProvenanceMarks } from "./FieldProvenanceMarks";
import { useRecordHistoryEntries } from "./RecordHistory";
import { enProvenanceMessages } from "./i18n";

/**
 * Provenance contributes field-statement marks to saved forms and one detailed
 * change source to the framework Activity stream. The source itself declines
 * resources without native history.
 */
const provenance = defineBaseAddon({
  id: "provenance",
  i18n: { provenance: enProvenanceMessages },
  containers: {
    "form#chrome": {
      "provenance.field-provenance": {
        sequence: 10,
        content: <FieldProvenanceMarks />,
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
