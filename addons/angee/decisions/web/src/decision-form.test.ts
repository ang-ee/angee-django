import { defaultWidgets, formSpecInitialValues, normalizeFormSpecValues } from "@angee/ui";
import { expect, test } from "vitest";
import { decisionFormActions } from "./decision-form";

const schema = {
  type: "object", properties: { action: { type: "string", options: [
    { value: "approve", label: "Approve", verdict: "completed" },
    { value: "deny", label: "Deny", verdict: "rejected" },
  ] } },
  oneOf: [
    { type: "object", properties: {
      action: { type: "string", const: "approve" },
      reference: { type: "string", readOnly: true, const: "retained", default: "retained" },
      amount: { type: "integer", default: 3 },
    }, required: ["action", "reference", "amount"] },
    { type: "object", properties: {
      action: { type: "string", const: "deny" }, reason: { type: "string", minLength: 1 },
    }, required: ["action", "reason"] },
  ],
};

test("frozen action branches use shared field defaults, read-only constants and normalization", () => {
  const [approve, deny] = decisionFormActions(schema, defaultWidgets);
  expect(approve?.label).toBe("Approve");
  expect(deny?.fields.map(({ name }) => name)).toEqual(["reason"]);
  expect(approve?.fields.find(({ name }) => name === "reference")?.readOnly).toBe(true);
  const fields = approve!.fields;
  expect(normalizeFormSpecValues(fields, formSpecInitialValues(fields, {}))).toEqual({ reference: "retained", amount: 3 });
  expect(fields.some(({ name }) => name === "action")).toBe(false);
});

test("ambiguous and absent frozen actions fail closed", () => {
  expect(() => decisionFormActions({}, defaultWidgets)).toThrow();
  expect(() => decisionFormActions({ ...schema, oneOf: [...schema.oneOf, schema.oneOf[0]] }, defaultWidgets)).toThrow();
  expect(() => decisionFormActions({ ...schema, oneOf: [schema.oneOf[1]] }, defaultWidgets)).toThrow();
});
