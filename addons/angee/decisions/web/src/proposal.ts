import * as v from "valibot";
import { JsonValueSchema } from "@angee/ui";

const RecordActionsSchema = v.strictObject({
  fields: v.optional(v.record(v.string(), v.strictObject({ set: JsonValueSchema })), {}),
  record: v.optional(v.strictObject({ call: v.pipe(v.string(), v.regex(/^[A-Za-z][A-Za-z0-9_]*$/)) })),
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

/** Mark fields named by any alternative of this record's open questions. */
export function fieldsToMark(decisions: readonly { verdict: unknown; proposal: unknown }[], recordId: string): string[] {
  const fields = new Set<string>();
  for (const decision of decisions) {
    if (decision.verdict !== null) continue;
    const parsed = v.safeParse(ProposalSchema, decision.proposal);
    if (parsed.success) for (const alternative of parsed.output.alternatives) {
      for (const name of Object.keys(alternative.actions[recordId]?.fields ?? {})) fields.add(name);
    }
  }
  return [...fields].sort();
}
