import { expect, test } from "vitest";
import * as v from "valibot";
import { decisionFieldMarks, ProposalSchema } from "./proposal";

const proposal = { multiple: true, alternatives: [
  { key: "rename", label: "Rename", outcome: "done", actions: { nte_1: { model: "knowledge.Note", fields: { title: { set: "Draft" }, body: { set: null } } } } },
  { key: "keep", label: "Keep", outcome: "done" },
  { key: "other", label: "Other record", outcome: "done", actions: { nte_2: { fields: { title: { set: "Other" } } } } },
] };

test("marks the union of alternative fields on open decisions for this record", () => {
  expect(decisionFieldMarks([{ is_open: true, proposal }, { is_open: false, proposal }], "nte_1").map(({ field }) => field).sort()).toEqual(["body", "title"]);
  expect(decisionFieldMarks([{ is_open: false, proposal }], "nte_1")).toEqual([]);
});
test("ignores malformed proposals and unrelated records", () => {
  expect(decisionFieldMarks([{ is_open: true, proposal: [] }, { is_open: true, proposal }], "unknown")).toEqual([]);
});
test("rejects empty and duplicate alternatives while preserving null sets", () => {
  expect(v.safeParse(ProposalSchema, { alternatives: [] }).success).toBe(false);
  expect(v.safeParse(ProposalSchema, { alternatives: [proposal.alternatives[0], proposal.alternatives[0]] }).success).toBe(false);
  expect(v.parse(ProposalSchema, proposal).alternatives[0]?.actions.nte_1?.fields.body?.set).toBeNull();
  expect(v.parse(ProposalSchema, proposal).alternatives[0]?.actions.nte_1?.model).toBe("knowledge.Note");
  expect(v.safeParse(ProposalSchema, { alternatives: [{ ...proposal.alternatives[0], actions: { nte_1: { model: "" } } }] }).success).toBe(false);
});

test("accepts choose with optional filters and refuses mixed or unknown field actions", () => {
  const withFields = (fields: unknown) => ({ alternatives: [{ key: "other", label: "Other", outcome: "done", actions: { nte_1: { fields } } }] });
  const fields = { parent: { choose: { filter: { name: { _neq: "Hidden" } } } }, title: { choose: {} } };
  expect(v.parse(ProposalSchema, withFields(fields)).alternatives[0]?.actions.nte_1?.fields).toEqual(fields);
  expect(decisionFieldMarks([{ is_open: true, proposal: withFields(fields) }], "nte_1")).toEqual([{ field: "parent" }, { field: "title" }]);
  for (const action of [{ choose: {}, set: "mixed" }, { choose: { unknown: true } }, { choose: { filter: [] } }, { unknown: true }])
    expect(v.safeParse(ProposalSchema, withFields({ parent: action })).success, JSON.stringify(action)).toBe(false);
});
