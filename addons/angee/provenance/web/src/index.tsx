import { defineBaseAddon } from "@angee/app";
import { Tab } from "@angee/ui";

import { HISTORY_TAB_ID, HistoryPane } from "./RecordHistory";
import { enProvenanceMessages } from "./i18n";

/**
 * The provenance addon's console surface: one History tab on every saved
 * record whose resource keeps native history (`resource.history`), composed
 * from the framework's record-tab seam exactly as integrate's Streams tab is.
 */
const provenance = defineBaseAddon({
  id: "provenance",
  i18n: { provenance: enProvenanceMessages },
  containers: {
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
  HistoryPane,
  HistoryTimeline,
  RecordHistoryTimeline,
  useRecordHistory,
  type HistoryTimelineProps,
  type RecordHistoryTimelineProps,
} from "./RecordHistory";
export type { HistoryEntry, HistoryFieldChange, HistoryStatement, RecordHistory } from "./documents.console";
export { entryTone, historyValue, statementVerb, type HistoryValue, type StatementVerb } from "./history-entries";

export default provenance;
