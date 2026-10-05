import { useMemo } from "react";
import { rowValueAtPath, type Row } from "@angee/metadata";
import { Badge, useRecordFieldMarks, type ListColumn, type ResourceToolbarFilterOption } from "@angee/ui";

import type { Decision } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { decisionFieldMarks } from "./proposal";

/** Opt in by adding this column; its field selects the server's derived boolean. */
export function decisionAttentionColumn<TRow extends Row>(): ListColumn<TRow> {
  return {
    field: "has_open_decisions", header: "Attention", sortable: false,
    render: (row) => rowValueAtPath(row, "has_open_decisions") === true
      ? <Badge tone="warning" density="compact">Needs attention</Badge> : null,
  };
}

export const openDecisionFilter: ResourceToolbarFilterOption = {
  id: "open-decisions", label: "Needs attention", filter: { has_open_decisions: { exact: true } },
};

/** Publish open-question marks even while the timeline is unmounted. */
export function useDecisionFieldMarks(records: readonly { record_model: string; record_id: string; decisions: readonly Decision[] }[], reveal: () => void) {
  const t = useDecisionsT();
  const publications = useMemo(() => records.map((entry) => ({
    model: entry.record_model, id: entry.record_id,
    marks: decisionFieldMarks(entry.decisions, entry.record_id).map(({ field }) => ({
      field, label: t("decision.unconfirmed"), tone: "warning" as const, onReveal: reveal,
    })),
  })), [records, reveal, t]);
  useRecordFieldMarks(publications);
}
