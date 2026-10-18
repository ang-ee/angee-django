import { defineBaseAddon } from "@angee/app";
import { Tab } from "@angee/ui";

import { FieldProvenanceMarks } from "./FieldProvenanceMarks";
import { HISTORY_TAB_ID, HistoryPane } from "./RecordHistory";
import { enProvenanceMessages } from "./i18n";

/**
 * The provenance addon's console surface: field-statement marks on saved forms
 * and one History tab when the resource keeps native history.
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
    "form#sections": {
      "provenance.history": {
        sequence: 90,
        content: (
          <Tab
            id={HISTORY_TAB_ID}
            label={{ namespace: "provenance", key: "history.title", fallback: enProvenanceMessages["history.title"] }}
            visibleWhen={(_record, resource) => resource?.history === true}
          >
            <HistoryPane />
          </Tab>
        ),
      },
    },
  },
});

export {
  HISTORY_TAB_ID,
  HistoryEntrySummary,
  RecordHistoryTimeline,
  useRecordHistory,
} from "./RecordHistory";

export default provenance;
