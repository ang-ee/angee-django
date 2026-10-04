import { expect, test } from "vitest";
import * as v from "valibot";
import { fieldsToMark, ProposalSchema } from "./proposal";

const proposal = { multiple: true, alternatives: [
  { key: "rename", label: "Rename", outcome: "done", actions: { nte_1: { fields: { title: { set: "Draft" }, body: { set: null } } } } },
  { key: "keep", label: "Keep", outcome: "done" },
  { key: "other", label: "Other record", outcome: "done", actions: { nte_2: { fields: { title: { set: "Other" } } } } },
] };

test("marks the union of alternative fields on open decisions for this record", () => {
  expect(fieldsToMark([{ verdict: null, proposal }, { verdict: ["rename"], proposal }], "nte_1")).toEqual(["body", "title"]);
  expect(fieldsToMark([{ verdict: ["rename"], proposal }], "nte_1")).toEqual([]);
});
test("ignores malformed proposals and unrelated records", () => {
  expect(fieldsToMark([{ verdict: null, proposal: [] }, { verdict: null, proposal }], "unknown")).toEqual([]);
});
test("rejects empty and duplicate alternatives while preserving null sets", () => {
  expect(v.safeParse(ProposalSchema, { alternatives: [] }).success).toBe(false);
  expect(v.safeParse(ProposalSchema, { alternatives: [proposal.alternatives[0], proposal.alternatives[0]] }).success).toBe(false);
  expect(v.parse(ProposalSchema, proposal).alternatives[0]?.actions.nte_1?.fields.body?.set).toBeNull();
});
