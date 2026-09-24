// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { defaultWidgets, formSpecInitialValues, type UiTranslate } from "@angee/ui";
import { useWorkflowsT } from "./i18n";
import { compileDecisionActionFormSpec } from "./decision-action-form";

let t: UiTranslate;
beforeAll(() => {
  t = renderHook(() => useWorkflowsT()).result.current;
});

const widgets = { ...defaultWidgets, facts: { read: () => null } };

test("provider-less workflow translations own plurals and fallback", () => {
  expect(t("inbox.validation.minLength", { label: "Note", count: 1 }))
    .toBe("Note must contain at least 1 character.");
  expect(t("inbox.validation.minLength", { label: "Note", count: 2 }))
    .toBe("Note must contain at least 2 characters.");
  expect(t("inbox.validation.invalidSchema")).toBe("The Decision schema is invalid.");
});

test("Decision annotations are parsed by workflows after the generic form boundary", () => {
  const schema = {
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["approve"], options: [
        { value: "approve", label: "Approve", verdict: "COMPLETE", confirm: "Continue?" },
      ] },
      reviewed: { type: "array", layout: "context", widget: "facts", items: { type: "object" } },
    },
    oneOf: [{
      type: "object", required: ["action"],
      properties: { action: { const: "approve" } }, additionalProperties: false,
    }],
  };
  expect(compileDecisionActionFormSpec(schema, widgets, t).options[0]).toEqual({
    value: "approve", label: "Approve", verdict: "COMPLETE", confirm: "Continue?",
  });
  expect(() => compileDecisionActionFormSpec({
    ...schema,
    properties: {
      ...schema.properties,
      action: { ...schema.properties.action, options: [
        { value: "approve", label: "Approve", verdict: "PENDING" },
      ] },
    },
  }, widgets, t)).toThrow("native verdict");
  expect(() => compileDecisionActionFormSpec({
    ...schema,
    properties: {
      ...schema.properties,
      action: { ...schema.properties.action, options: [
        { value: "approve", label: "Approve", verdict: "COMPLETE", unsupported: true },
      ] },
    },
  }, widgets, t)).toThrow("unique enum value");
  expect(() => compileDecisionActionFormSpec({ ...schema, type: "unsupported" }, widgets, t))
    .toThrow("The Decision schema is invalid.");
});

test("native counterparty, identity, and source action schemas retain exact branch values and full constraints", () => {
  const counterpartyReview = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["approve", "reject", "escalate"], options: [
        { value: "approve", label: "Select this counterparty", verdict: "COMPLETE" },
        { value: "reject", label: "Reject counterparty", verdict: "REJECT", variant: "destructive", confirm: "Reject?" },
        { value: "escalate", label: "Escalate for review", verdict: "ESCALATE" },
      ] },
      party_id: { type: "string", relation: { resource: "parties.Party", permission: "read" } },
      note: { type: "string", minLength: 1 },
      reviewed: { type: "array", layout: "context", widget: "facts", items: { type: "object" } },
    },
    oneOf: [
      { type: "object", required: ["action", "party_id"], properties: {
        action: { const: "approve" },
        party_id: { type: "string", relation: { resource: "parties.Party", permission: "read" } },
        note: { type: "string", minLength: 1 },
      }, additionalProperties: false },
      { type: "object", required: ["action", "note"], properties: {
        action: { const: "reject" }, note: { type: "string", minLength: 1 },
      }, additionalProperties: false },
      { type: "object", required: ["action", "note"], properties: {
        action: { const: "escalate" }, note: { type: "string", minLength: 1 },
      }, additionalProperties: false },
    ],
  }, widgets, t);
  expect(counterpartyReview.fieldsFor("approve")[0]).toEqual(expect.objectContaining({
    name: "party_id",
    relation: { resource: "parties.Party", permission: "read" },
  }));
  expect(counterpartyReview.options.map((option) => option.verdict)).toEqual(["COMPLETE", "REJECT", "ESCALATE"]);
  expect(counterpartyReview.project("reject", { party_id: "pty_existing", note: "", reviewed: [] }))
    .toEqual({ action: "reject", note: "" });
  expect(counterpartyReview.validate({ action: "reject", note: "" }).valid).toBe(false);
  expect(counterpartyReview.validate({ action: "reject", note: "Reason" }).valid).toBe(true);
  expect(counterpartyReview.validateContext({ reviewed: [] }).valid).toBe(true);
  expect(counterpartyReview.validateContext({})).toEqual({
    valid: false,
    messages: { reviewed: ["Frozen Decision context is missing."] },
  });
  expect(counterpartyReview.validateContext({ reviewed: "invalid" })).toEqual({
    valid: false,
    messages: { reviewed: ["Frozen Decision context is invalid."] },
  });

  const identity = compileDecisionActionFormSpec({
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
  }, widgets, t);
  const frozenDigest = "a".repeat(64);
  expect(identity.project("verify", { expected_identity_digest: frozenDigest, evidence: null, reviewed: [{ pointer: "/identity" }] }))
    .toEqual({ action: "verify", expected_identity_digest: frozenDigest, evidence: null });
  expect(identity.validate(identity.project("verify", { expected_identity_digest: frozenDigest, evidence: null })).valid).toBe(true);
  expect(identity.validate({ action: "verify", expected_identity_digest: "wrong", evidence: null }).valid).toBe(false);

  const source = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["link_existing", "keep_separate"], options: [
        { value: "link_existing", label: "Link this source", verdict: "COMPLETE" },
        { value: "keep_separate", label: "Keep separate", verdict: "COMPLETE" },
      ] },
      document_id: { type: "string", minLength: 1 }, reason: { type: "string", pattern: ".*\\S.*" },
      approved: { type: "boolean" }, count: { type: "integer" },
      reviewed: { type: "array", layout: "context", widget: "facts", items: { type: "object" } },
    },
    oneOf: [
      { type: "object", required: ["action", "document_id", "reason"], properties: {
        action: { const: "link_existing" }, document_id: { type: "string", minLength: 1 },
        reason: { type: "string", pattern: ".*\\S.*" }, approved: { type: "boolean" }, count: { type: "integer" },
      }, additionalProperties: false },
      { type: "object", required: ["action", "reason"], properties: {
        action: { const: "keep_separate" }, reason: { type: "string", pattern: ".*\\S.*" },
      }, additionalProperties: false },
    ],
  }, widgets, t);
  expect(source.project("link_existing", { document_id: "doc_current", reason: "Source reviewed", approved: false, count: 0, reviewed: [] }))
    .toEqual({ action: "link_existing", document_id: "doc_current", reason: "Source reviewed", approved: false, count: 0 });
  expect(source.validate(source.project("link_existing", { document_id: "doc_current", reason: "Source reviewed", approved: false, count: 0 })).valid).toBe(true);
  expect(source.validate({ action: "keep_separate", reason: "   " }).valid).toBe(false);
});

test("action branches consume the complete relation descriptors emitted by the Decision owner", () => {
  const frozen = "pty_frozen";
  const form = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["bind", "other"], options: [
        { value: "bind", label: "Select candidate", verdict: "COMPLETE" },
        { value: "other", label: "Select another", verdict: "COMPLETE" },
      ] },
      party_id: {
        type: "string", label: "Party", defaultValue: frozen,
        relation: {
          resource: "parties.Party", permission: "read",
          create: { resource: "parties.Organization", actionLabel: "Create party" },
        },
      },
    },
    oneOf: [
      { type: "object", required: ["action", "party_id"], properties: {
        action: { const: "bind" },
        party_id: {
          type: "string", label: "Party", defaultValue: frozen, enum: [frozen],
          relation: {
            resource: "parties.Party", permission: "read",
            filters: [{ field: "id", operator: "in", value: [frozen] }],
          },
        },
      }, additionalProperties: false },
      { type: "object", required: ["action", "party_id"], properties: {
        action: { const: "other" },
        party_id: {
          type: "string", label: "Party", defaultValue: frozen, not: { enum: [frozen] },
          relation: {
            resource: "parties.Party", permission: "read",
            filters: [{ field: "id", operator: "nin", value: [frozen] }],
            create: { resource: "parties.Organization", actionLabel: "Create party" },
          },
        },
      }, additionalProperties: false },
    ],
  }, widgets, t);

  expect(form.fieldsFor("bind")).toEqual([expect.objectContaining({
    name: "party_id",
    required: true,
    label: "Party",
    defaultValue: frozen,
    options: [{ value: frozen, label: frozen }],
    relation: {
      resource: "parties.Party", permission: "read",
      filters: [{ field: "id", operator: "in", value: [frozen] }],
    },
  })]);
  expect(form.fieldsFor("other")).toEqual([expect.objectContaining({
    name: "party_id",
    required: true,
    label: "Party",
    defaultValue: frozen,
    relation: {
      resource: "parties.Party", permission: "read",
      filters: [{ field: "id", operator: "nin", value: [frozen] }],
      create: { resource: "parties.Organization", actionLabel: "Create party" },
    },
  })]);
  expect(form.validate({ action: "bind", party_id: frozen }).valid).toBe(true);
  expect(form.validate({ action: "bind", party_id: "pty_other" }).valid).toBe(false);
  expect(form.validate({ action: "other", party_id: "pty_other" }).valid).toBe(true);
  expect(form.validate(form.project("other", { party_id: frozen })).valid).toBe(false);
});

test("alternative correction fields produce one labelled instruction without duplicate branch errors", () => {
  const form = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["correct", "reject"], options: [
        { value: "correct", label: "Correct source facts", verdict: "COMPLETE" },
        { value: "reject", label: "Reject document", verdict: "REJECT" },
      ] },
      note: { type: "string", label: "Review explanation", minLength: 1 },
      currency: { type: ["string", "null"], label: "Document currency", omittable: true },
      document_date: { type: ["string", "null"], label: "Document date", omittable: true },
      counterparty_name: { type: ["string", "null"], label: "Counterparty name", omittable: true },
    },
    oneOf: [
      { type: "object", required: ["action", "note"], properties: {
        action: { const: "correct" }, note: { type: "string", label: "Review explanation", minLength: 1 },
        currency: { type: ["string", "null"], label: "Document currency", omittable: true },
        document_date: { type: ["string", "null"], label: "Document date", omittable: true },
        counterparty_name: { type: ["string", "null"], label: "Counterparty name", omittable: true },
      }, anyOf: [
        { required: ["currency"], properties: { currency: { type: "string", minLength: 1 } } },
        { required: ["document_date"], properties: { document_date: { type: "string", minLength: 1 } } },
        { required: ["counterparty_name"], properties: { counterparty_name: { type: "string", minLength: 1 } } },
      ], additionalProperties: false },
      { type: "object", required: ["action", "note"], properties: {
        action: { const: "reject" }, note: { type: "string", label: "Review explanation", minLength: 1 },
      }, additionalProperties: false },
    ],
  }, widgets, t);

  expect(form.validate({
    action: "correct", note: "", currency: null, document_date: null, counterparty_name: null,
  })).toEqual({
    valid: false,
    messages: {
      note: ["Review explanation must contain at least 1 character."],
      root: ["Complete at least one of: Document currency, Document date, or Counterparty name."],
    },
  });
  expect(form.validate({
    action: "correct", note: "Reviewed", currency: null, document_date: null, counterparty_name: null,
  }).messages).toEqual({
    root: ["Complete at least one of: Document currency, Document date, or Counterparty name."],
  });
  expect(form.validate({
    action: "correct", note: "Reviewed", currency: null, document_date: "2026-08-31", counterparty_name: null,
  })).toEqual({ valid: true, messages: {} });
});

test("nested Decision errors use full dotted paths with bare messages", () => {
  const form = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["apply"], options: [
        { value: "apply", label: "Apply mapping", verdict: "COMPLETE" },
      ] },
      documents: { type: "array", items: { type: "object", properties: {
        lines: { type: "array", items: { type: "object", properties: {
          account_id: { type: "string", label: "Expense account" },
        } } },
      } } },
    },
    oneOf: [{ type: "object", required: ["action", "documents"], properties: {
      action: { const: "apply" },
      documents: { type: "array", items: { type: "object", properties: {
        lines: { type: "array", items: { type: "object", properties: {
          account_id: { type: "string", label: "Expense account" },
        } } },
      } } },
    }, additionalProperties: false }],
  }, widgets, t);

  expect(form.validate({
    action: "apply", documents: [{ lines: [{ account_id: null }] }],
  })).toEqual({
    valid: false,
    messages: {
      "documents.0.lines.0.account_id": ["account_id has an invalid value."],
    },
  });
});

test.each(["$defs", "definitions"])("resolves %s row references without losing Decision constraints or annotations", (definitions) => {
  const choices = { type: "array", label: "Choices", items: { $ref: `#/${definitions}/Choice` } };
  const form = compileDecisionActionFormSpec({
    type: "object", required: ["action"], propertyOrder: ["action", "reviewed", "choices"], properties: {
      action: { type: "string", enum: ["apply", "reject"], options: [
        { value: "apply", label: "Apply choices", verdict: "COMPLETE" },
        { value: "reject", label: "Reject", verdict: "REJECT" },
      ] },
      choices,
      reviewed: { ...choices, layout: "context", widget: "facts" },
    },
    [definitions]: {
      Choice: { type: "object", required: ["identity", "reason"], properties: {
        identity: { $ref: `#/${definitions}/Identity`, label: "Selected identity" },
        reason: { type: "string", label: "Reason", placeholder: "Explain the choice", minLength: 1 },
      }, additionalProperties: false },
      Identity: { type: "string", enum: ["first", "second"], options: [
        { value: "first", label: "First identity" },
        { value: "second", label: "Second identity" },
      ] },
    },
    oneOf: [
      { type: "object", required: ["action", "choices"], properties: {
        action: { const: "apply" }, choices,
      }, additionalProperties: false },
      { type: "object", required: ["action"], properties: {
        action: { const: "reject" },
      }, additionalProperties: false },
    ],
  }, widgets, t);

  expect(form.fieldsFor("apply")).toEqual([{
    name: "choices", kind: "array", widget: "rows", label: "Choices", required: true,
    rowTemplate: [
      { name: "identity", kind: "string", widget: "select", label: "Selected identity", required: true,
        options: [
          { value: "first", label: "First identity" },
          { value: "second", label: "Second identity" },
        ] },
      { name: "reason", kind: "string", widget: "text", label: "Reason", required: true,
        placeholder: "Explain the choice", minLength: 1 },
    ],
  }]);
  const values = { choices: [{ identity: "first", reason: "Reviewed" }] };
  expect(form.project("apply", values)).toEqual({ action: "apply", ...values });
  expect(form.validate(form.project("apply", values))).toEqual({ valid: true, messages: {} });
  expect(form.validate({ action: "apply", choices: [{ identity: "unknown", reason: "Reviewed" }] }))
    .toEqual({ valid: false, messages: { "choices.0.identity": ["identity has an invalid value."] } });
  expect(form.validate({ action: "apply", choices: [{ identity: "first", reason: "" }] }))
    .toEqual({ valid: false, messages: { "choices.0.reason": ["reason must contain at least 1 character."] } });
  expect(form.fieldsFor("reject")).toEqual([]);
  expect(form.project("reject", values)).toEqual({ action: "reject" });
  expect(form.validate(form.project("reject", values))).toEqual({ valid: true, messages: {} });
  expect(form.validateContext({ reviewed: values.choices })).toEqual({ valid: true, messages: {} });
  expect(form.validateContext({ reviewed: [{ identity: "unknown", reason: "Reviewed" }] }))
    .toEqual({ valid: false, messages: { reviewed: ["Frozen Decision context is invalid."] } });
});

test("retains omitted optional reasons inside a structured Decision object", () => {
  const form = compileDecisionActionFormSpec({
    type: "object", required: ["action"], properties: {
      action: { type: "string", enum: ["apply"], options: [
        { value: "apply", label: "Apply mapping", verdict: "COMPLETE" },
      ] },
      retired_reasons: { type: "object", widget: "object", properties: {
        source_1: { type: "string", minLength: 1 },
        source_2: { type: "string", minLength: 1 },
      }, additionalProperties: false },
    },
    oneOf: [{ type: "object", required: ["action", "retired_reasons"], properties: {
      action: { const: "apply" },
      retired_reasons: { type: "object", widget: "object", properties: {
        source_1: { type: "string", minLength: 1 },
        source_2: { type: "string", minLength: 1 },
      }, additionalProperties: false },
    }, additionalProperties: false }],
  }, widgets, t);

  const initial = formSpecInitialValues(form.inputFields, { retired_reasons: {} });
  expect(initial).toEqual({ retired_reasons: {} });
  expect(form.validate(form.project("apply", initial))).toEqual({ valid: true, messages: {} });
});
