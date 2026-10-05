import * as v from "valibot";
import { JsonValueSchema } from "@angee/ui";

const RecordActionsSchema = v.strictObject({
  model: v.optional(v.pipe(v.string(), v.minLength(1))),
  fields: v.optional(v.record(v.string(), v.strictObject({ set: v.optional(JsonValueSchema) })), {}),
  record: v.optional(v.strictObject({ call: v.pipe(v.string(), v.regex(/^[A-Za-z][A-Za-z0-9_]*$/)), arguments: v.optional(v.record(v.string(), JsonValueSchema), {}) })),
});

/** Alternatives are the sole source of labels, changes and continuation outcomes. */
export const ProposalSchema = v.strictObject({
  multiple: v.optional(v.boolean(), false),
  alternatives: v.pipe(v.array(v.strictObject({
    key: v.pipe(v.string(), v.minLength(1)),
    label: v.pipe(v.string(), v.minLength(1)),
    actions: v.optional(v.record(v.string(), RecordActionsSchema), {}),
    outcome: v.pipe(v.string(), v.minLength(1)),
  })), v.minLength(1), v.check((alternatives) => new Set(alternatives.map(({ key }) => key)).size === alternatives.length)),
});

export function decisionFieldMarks(decisions: readonly { is_open: boolean; proposal: unknown }[], recordId: string) {
  const marks: { field: string }[] = [];
  for (const decision of decisions) {
    if (!decision.is_open) continue;
    const parsed = v.safeParse(ProposalSchema, decision.proposal);
    if (parsed.success) {
      for (const alternative of parsed.output.alternatives) {
        for (const field of Object.keys(alternative.actions[recordId]?.fields ?? {})) {
          if (!marks.some((mark) => mark.field === field))
            marks.push({ field });
        }
      }
    }
  }
  return marks;
}
