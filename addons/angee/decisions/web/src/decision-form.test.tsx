// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { defaultWidgets } from "@angee/ui";

import { decisionForm } from "./decision-form";
import { useDecisionsT } from "./i18n";
import { decisionFixture } from "./testing";

describe("frozen decision form", () => {
  test("keeps the discriminator separate and reuses each projected branch", () => {
    const { result } = renderHook(() => decisionForm(decisionFixture().form_schema, defaultWidgets, useDecisionsT()));
    const definition = result.current;
    expect(definition.actionFields.map((field) => field.name)).toEqual(["action"]);
    expect(definition.fields("accept").map((field) => field.name)).toEqual(["note", "reference"]);
    expect(definition.fields("reject").map((field) => field.name)).toEqual(["reason"]);
    expect(definition.fields("accept")).toBe(definition.fields("accept"));
    expect(definition.fieldNames).toEqual(["action", "note", "reference", "reason"]);
    expect(definition.actionFields[0]?.branchReset?.("accept")).toEqual({
      fields: ["note", "reference"], values: { note: "Read", reference: "R-7" },
    });
    expect(definition.actionFields[0]?.branchReset?.("reject")).toEqual({
      fields: ["reason"], values: { reason: "" },
    });
  });

  test("accepts backend-frozen array defaults with prefixItems and enforces per-row constants", async () => {
    const row = { type: "object", additionalProperties: false, required: ["source"], properties: { source: { type: "string", readOnly: true } } };
    const { result } = renderHook(() => decisionForm({
      type: "object", properties: { action: { type: "string", enum: ["accept"], options: [{ value: "accept", label: "Accept" }] } },
      required: ["action"], discriminator: { propertyName: "action" },
      oneOf: [{ type: "object", additionalProperties: false, required: ["action", "rows"], properties: {
        action: { type: "string", const: "accept" },
        rows: { type: "array", default: [{ source: "Retained" }], items: row,
          prefixItems: [{ ...row, properties: { source: { type: "string", readOnly: true, default: "Retained", const: "Retained" } } }] },
      } }],
    }, defaultWidgets, useDecisionsT()));
    const options = { fields: {}, shouldUseNativeValidation: false };
    const valid = await result.current.resolver(result.current.initial(), undefined, options);
    expect(valid.errors).toEqual({});
    const invalid = await result.current.resolver({ action: "accept", rows: [{ source: "Changed" }] }, undefined, options);
    expect(invalid.errors).toMatchObject({ rows: [{ source: { message: "Keep the supplied value." } }] });
  });
});
