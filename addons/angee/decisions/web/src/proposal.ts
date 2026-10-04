import * as v from "valibot";
import { JsonValueSchema } from "@angee/ui";

const RecordActionsSchema = v.strictObject({
  fields: v.optional(v.record(v.string(), v.strictObject({ set: JsonValueSchema })), {}),
  record: v.optional(v.strictObject({ call: v.pipe(v.string(), v.regex(/^[A-Za-z][A-Za-z0-9_]*$/)), arguments: v.optional(v.record(v.string(), JsonValueSchema), {}) })),
});

/** Alternatives are the sole source of labels, changes and continuation outcomes. */
export const ProposalSchema = v.strictObject({
  multiple: v.optional(v.boolean(), false),
  checks: v.optional(v.record(v.string(), v.array(v.string())), {}),
  alternatives: v.pipe(v.array(v.strictObject({
    key: v.pipe(v.string(), v.minLength(1)),
    label: v.pipe(v.string(), v.minLength(1)),
    actions: v.optional(v.record(v.string(), RecordActionsSchema), {}),
    outcome: v.pipe(v.string(), v.minLength(1)),
  })), v.minLength(1), v.check((alternatives) => new Set(alternatives.map(({ key }) => key)).size === alternatives.length)),
});

/** Mark fields named by any alternative of this record's open questions. */
export function fieldsToMark(decisions: readonly { is_open: boolean; proposal: unknown }[], recordId: string): string[] {
  return [...new Set(decisionFieldMarks(decisions, recordId).map(({ field }) => field))].sort();
}

export function decisionFieldMarks(decisions: readonly { id?: string; is_open: boolean; proposal: unknown }[], recordId: string) {
  const marks: { field: string; decision: string; kind: "unconfirmed" | "check" }[] = [];
  for (const decision of decisions) {
    if (!decision.is_open) continue;
    const parsed = v.safeParse(ProposalSchema, decision.proposal);
    if (parsed.success) {
      for (const field of parsed.output.checks[recordId] ?? []) marks.push({ field, decision: decision.id ?? "", kind: "check" });
      for (const alternative of parsed.output.alternatives) {
        for (const field of Object.keys(alternative.actions[recordId]?.fields ?? {})) {
          if (!marks.some((mark) => mark.field === field && mark.kind === "check"))
            marks.push({ field, decision: decision.id ?? "", kind: "unconfirmed" });
        }
      }
    }
  }
  return marks;
}
