import { useMemo } from "react";
import { rowValueAtPath, type Row } from "@angee/metadata";
import { Badge, useRecordFieldMarks, type ListColumn, type ResourceToolbarFilterOption } from "@angee/ui";

import type { Decision } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { decisionFieldMarks } from "./proposal";

/** Opt in by adding this column; its field selects the server's derived boolean. */
export function decisionAttentionColumn<TRow extends Row>(): ListColumn<TRow> {
  return {
    field: "has_open_decisions", header: <AttentionLabel />, sortable: false,
    render: (row) => rowValueAtPath(row, "has_open_decisions") === true
      ? <Badge tone="warning" density="compact"><AttentionLabel needs /></Badge> : null,
  };
}

export const openDecisionFilter: ResourceToolbarFilterOption = {
  id: "open-decisions", label: <AttentionLabel needs />, filter: { has_open_decisions: { exact: true } },
};

function AttentionLabel({ needs = false }: { needs?: boolean }) { const t = useDecisionsT(); return <>{t(needs ? "attention.needs" : "attention.title")}</>; }

/** Publish open-question marks even while the timeline is unmounted. */
export function useDecisionFieldMarks(records: readonly { record_model: string; record_id: string; decisions: readonly Pick<Decision, "id" | "is_open" | "proposal">[] }[], reveal: () => void) {
  const t = useDecisionsT();
  const publications = useMemo(() => records.map((entry) => ({
    model: entry.record_model, id: entry.record_id,
    marks: decisionFieldMarks(entry.decisions, entry.record_id).map(({ field }) => ({
      field, label: t("decision.unconfirmed"), tone: "warning" as const, onReveal: reveal,
    })),
  })), [records, reveal, t]);
  useRecordFieldMarks(publications);
}
