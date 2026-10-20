import { useMemo } from "react";
import { rowValueAtPath, type Row } from "@angee/metadata";
import { Chip, useRecordFieldMarks, useStatusTone, type ListColumn, type ResourceToolbarFilterOption } from "@angee/ui";

import type { Decision } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { decisionFieldMarks } from "./proposal";

interface AttentionTag {
  id: string;
  name: string;
  attention: boolean;
}

const OPEN_DECISION_TONE = "needs-decision";

/** Opt in by adding the record's one decision-and-tag attention cluster. */
export function attentionColumn<TRow extends Row>(): ListColumn<TRow> {
  return {
    field: "has_open_decisions", header: <AttentionLabel />, sortable: false,
    selectionPaths: ["has_open_decisions", "tags.id", "tags.name", "tags.attention"],
    render: (row) => <AttentionCell row={row} />,
  };
}

export const openDecisionFilter: ResourceToolbarFilterOption = {
  id: "open-decisions", label: <AttentionLabel needs />, filter: { has_open_decisions: { exact: true } },
};

function AttentionLabel({ needs = false }: { needs?: boolean }) { const t = useDecisionsT(); return <>{t(needs ? "attention.needs" : "attention.title")}</>; }

function AttentionCell({ row }: { row: Row }) {
  const tone = useStatusTone();
  const open = rowValueAtPath(row, "has_open_decisions") === true;
  const value = rowValueAtPath(row, "tags");
  const tags = Array.isArray(value) ? value.filter(isAttentionTag) : [];
  if (!open && tags.length === 0) return null;
  return <span className="inline-flex flex-wrap items-center gap-1">
    {open ? <Chip tone={tone(OPEN_DECISION_TONE)}><AttentionLabel needs /></Chip> : null}
    {tags.map((tag) => <Chip key={tag.id} tone={tone(tag.name)}>{tag.name}</Chip>)}
  </span>;
}

function isAttentionTag(value: unknown): value is AttentionTag {
  return value != null && typeof value === "object"
    && (value as { attention?: unknown }).attention === true
    && typeof (value as { id?: unknown }).id === "string"
    && typeof (value as { name?: unknown }).name === "string";
}

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
