import type { ComparisonAnswer, ComparisonProposal, ComparisonTopic } from "./comparison-data";

export type ComparisonRow =
  | { id: string; kind: "topic"; topic: ComparisonTopic }
  | { id: string; kind: "fact"; field: ComparisonFactField };

export type ComparisonFactField =
  | "cost"
  | "staffing"
  | "timeframe_start"
  | "timeframe_end"
  | "confidence"
  | "valid_until";

const FACT_ROWS: readonly ComparisonRow[] = [
  { id: "fact:cost", kind: "fact", field: "cost" },
  { id: "fact:staffing", kind: "fact", field: "staffing" },
  { id: "fact:timeframe_start", kind: "fact", field: "timeframe_start" },
  { id: "fact:timeframe_end", kind: "fact", field: "timeframe_end" },
  { id: "fact:confidence", kind: "fact", field: "confidence" },
  { id: "fact:valid_until", kind: "fact", field: "valid_until" },
];

/** Topic-first comparison rows, with deterministic sort-order/key tie breaking. */
export function comparisonRows(
  topics: readonly ComparisonTopic[],
): readonly ComparisonRow[] {
  const ordered = [...topics].sort(
    (left, right) =>
      numericOrder(left.sort_order) - numericOrder(right.sort_order)
      || String(left.key ?? "").localeCompare(String(right.key ?? ""))
      || left.id.localeCompare(right.id),
  );
  return [
    ...ordered.map(
      (topic): ComparisonRow => ({
        id: `topic:${topic.id}`,
        kind: "topic",
        topic,
      }),
    ),
    ...FACT_ROWS,
  ];
}

/** Read one matrix cell without interpreting null as permission or redaction. */
export function comparisonCellValue(
  row: ComparisonRow,
  proposal: ComparisonProposal,
  answers: readonly ComparisonAnswer[],
): unknown {
  if (row.kind === "fact") return proposal[row.field];
  return answers.find(
    (answer) =>
      answer.proposal?.id === proposal.id && answer.topic?.id === row.topic.id,
  )?.body;
}

/** Redacted and genuinely empty values intentionally share one display branch. */
export function isEmptyComparisonValue(value: unknown): boolean {
  return value == null || (typeof value === "string" && value.trim() === "");
}

export function proposalColumnLabel(proposal: ComparisonProposal): string {
  const party = String(proposal.party?.display_name ?? "").trim();
  if (party) return party;
  const responder = responderLabel(proposal.responder);
  return responder || proposal.id;
}

function responderLabel(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  return String((value as { display_name?: unknown }).display_name ?? "").trim();
}

function numericOrder(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}
