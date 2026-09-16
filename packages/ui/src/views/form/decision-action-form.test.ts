import { expect, test } from "vitest";
import { defaultWidgets } from "../../widgets";
import { compileDecisionActionFormSpec } from "./form-spec";

const widgets = { ...defaultWidgets, facts: { read: () => null } };

test("native terms, bank, and source action schemas retain exact branch values and full constraints", () => {
  const terms = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["approve", "reject", "escalate"], options: [
        { value: "approve", label: "Select this supplier", verdict: "COMPLETE" },
        { value: "reject", label: "Reject supplier", verdict: "REJECT", variant: "destructive", confirm: "Reject?" },
        { value: "escalate", label: "Escalate for review", verdict: "ESCALATE" },
      ] },
      party_id: { type: "string", relation: { resource: "parties.Party", permission: "read" } },
      note: { type: "string", minLength: 1 },
      reviewed: { type: "array", layout: "context", widget: "facts", items: { type: "object" } },
    },
    oneOf: [
      { type: "object", required: ["action", "party_id"], properties: {
        action: { const: "approve" }, party_id: { type: "string" }, note: { type: "string" },
      }, additionalProperties: false },
      { type: "object", required: ["action", "note"], properties: {
        action: { const: "reject" }, note: { type: "string", minLength: 1 },
      }, additionalProperties: false },
      { type: "object", required: ["action", "note"], properties: {
        action: { const: "escalate" }, note: { type: "string", minLength: 1 },
      }, additionalProperties: false },
    ],
  }, widgets);
  expect(terms.options.map((option) => option.verdict)).toEqual(["COMPLETE", "REJECT", "ESCALATE"]);
  expect(terms.project("reject", { party_id: "pty_existing", note: "", reviewed: [] }))
    .toEqual({ action: "reject", note: "" });
  expect(terms.validate({ action: "reject", note: "" }).valid).toBe(false);
  expect(terms.validate({ action: "reject", note: "Reason" }).valid).toBe(true);
  expect(terms.validateContext({ reviewed: [] }).valid).toBe(true);

  const bank = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["verify", "reject"], options: [
        { value: "verify", label: "Verify destination", verdict: "COMPLETE" },
        { value: "reject", label: "Reject instructions", verdict: "REJECT" },
      ] },
      expected_identity_digest: { type: "string", pattern: "^[0-9a-f]{64}$" },
      evidence: { type: ["string", "null"] },
      reviewed: { type: "array", layout: "context", widget: "facts", items: { type: "object" } },
    },
    oneOf: [
      { type: "object", required: ["action", "expected_identity_digest"], properties: {
        action: { const: "verify" }, expected_identity_digest: { type: "string", pattern: "^[0-9a-f]{64}$" },
        evidence: { type: ["string", "null"] },
      }, additionalProperties: false },
      { type: "object", required: ["action"], properties: { action: { const: "reject" } }, additionalProperties: false },
    ],
  }, widgets);
  const frozenDigest = "a".repeat(64);
  expect(bank.project("verify", { expected_identity_digest: frozenDigest, evidence: null, reviewed: [{ pointer: "/bank" }] }))
    .toEqual({ action: "verify", expected_identity_digest: frozenDigest, evidence: null });
  expect(bank.validate(bank.project("verify", { expected_identity_digest: frozenDigest, evidence: null })).valid).toBe(true);
  expect(bank.validate({ action: "verify", expected_identity_digest: "wrong", evidence: null }).valid).toBe(false);

  const source = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["link_existing", "keep_separate"], options: [
        { value: "link_existing", label: "Link this source", verdict: "COMPLETE" },
        { value: "keep_separate", label: "Keep separate", verdict: "COMPLETE" },
      ] },
      invoice_id: { type: "string", minLength: 1 }, reason: { type: "string", pattern: ".*\\S.*" },
      approved: { type: "boolean" }, count: { type: "integer" },
      reviewed: { type: "array", layout: "context", widget: "facts", items: { type: "object" } },
    },
    oneOf: [
      { type: "object", required: ["action", "invoice_id", "reason"], properties: {
        action: { const: "link_existing" }, invoice_id: { type: "string", minLength: 1 },
        reason: { type: "string", pattern: ".*\\S.*" }, approved: { type: "boolean" }, count: { type: "integer" },
      }, additionalProperties: false },
      { type: "object", required: ["action", "reason"], properties: {
        action: { const: "keep_separate" }, reason: { type: "string", pattern: ".*\\S.*" },
      }, additionalProperties: false },
    ],
  }, widgets);
  expect(source.project("link_existing", { invoice_id: "inv_current", reason: "Source reviewed", approved: false, count: 0, reviewed: [] }))
    .toEqual({ action: "link_existing", invoice_id: "inv_current", reason: "Source reviewed", approved: false, count: 0 });
  expect(source.validate(source.project("link_existing", { invoice_id: "inv_current", reason: "Source reviewed", approved: false, count: 0 })).valid).toBe(true);
  expect(source.validate({ action: "keep_separate", reason: "   " }).valid).toBe(false);
});
