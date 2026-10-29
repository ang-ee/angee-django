import { defineBaseAddon } from "@angee/app";
import { History } from "lucide-react";

import { FieldProvenanceMarks } from "./FieldProvenanceMarks";
import { RecordHistoryTimeline } from "./RecordHistory";
import { enProvenanceMessages } from "./i18n";

/**
 * Provenance contributes field-statement marks to saved forms, the change kind
 * on the Timeline, and the history drill-in its lines open; the Python source
 * declines resources without native history.
 */
const provenance = defineBaseAddon({
  id: "provenance",
  icons: { history: History },
  i18n: { provenance: enProvenanceMessages },
  // How a changeset's activity and outcome read on the history rows: the one composed
  // status vocabulary decides the tone, never a map of this addon's own.
  statusTones: { manual: "success", pull: "success", push: "success", import: "success", merge: "info", revert: "warning", seed: "neutral", system: "neutral", failure: "danger" },
  containers: {
    "provenance#statement-references": { models: true },
    "form#chrome": {
      "provenance.field-provenance": {
        sequence: 10,
        content: <FieldProvenanceMarks />,
      },
    },
    "timeline#kinds": {
      "provenance.change": { content: { label: "Changes", icon: "pencil", tone: "muted", verb: "View change", target: { tab: "provenance.history", focus: "changeset" } } },
    },
    "record#aside": {
      "provenance.history": {
        sequence: 13,
        content: {
          label: "History", icon: "history", panelClassName: "p-0", strip: false, target: { model: "provenance.Changeset" },
          when: (context) => Boolean(context.route?.history),
          render: (context, selection) => (
            <RecordHistoryTimeline
              model={context.route?.canonicalLabel ?? context.route?.modelLabel ?? ""}
              id={context.view.kind === "record" ? context.view.sqid ?? "" : ""}
              selected={selection?.target}
            />
          ),
        },
      },
    },
  },
});

export { type StatementReferenceProps, RecordHistoryTimeline, useRecordHistory } from "./RecordHistory";

export default provenance;
