import { describe, expect, test } from "vitest";
import type { ResolverOptions } from "react-hook-form";

import { createAngeeI18nInstance } from "../../runtime/i18n";
import { defaultWidgets } from "../../widgets";
import { ajvValidationErrors, createJsonSchemaAjv, createJsonSchemaResolver, jsonSchemaActionArgs } from "./json-schema";

const resolverOptions: ResolverOptions<Record<string, unknown>> = {
  fields: {}, shouldUseNativeValidation: false,
};

describe("JSON Schema forms", () => {
  test("projects a selected action branch, resets its fields and validates typed acknowledgement", async () => {
    const args = jsonSchemaActionArgs({
      type: "object", discriminator: { propertyName: "kind" }, required: ["kind"],
      $defs: { count: { type: "integer", minimum: 1, default: 2 } },
      properties: { kind: { type: "string", enum: ["count", "approve"] } },
      oneOf: [
        { properties: { kind: { const: "count" }, count: { $ref: "#/$defs/count" } }, required: ["count"] },
        { properties: { kind: { const: "approve" }, acknowledge: { type: "boolean", const: true } }, required: ["acknowledge"] },
      ],
    }, defaultWidgets);
    expect(args.defaultValues).toEqual({ kind: "count", count: 2 });
    expect(args.fieldNames).toEqual(["kind", "count", "acknowledge"]);
    if (typeof args.fields !== "function") throw new Error("Expected dynamic fields");
    const fields = args.fields({ kind: "approve" });
    expect(fields.map((field) => field.name)).toEqual(["kind", "acknowledge"]);
    expect(fields[0]?.branchReset?.("approve")).toEqual({ fields: ["acknowledge"], values: { acknowledge: false } });
    expect((await args.resolver!({ kind: "approve", acknowledge: false }, undefined, resolverOptions)).errors)
      .toMatchObject({ acknowledge: { message: "Keep the supplied value." } });
    expect(await args.resolver!({ kind: "approve", acknowledge: true, count: 7 }, undefined, resolverOptions))
      .toEqual({ values: { kind: "approve", acknowledge: true }, errors: {} });
  });

  test("retains schema defaults, nested values and readonly controls in action args", () => {
    const args = jsonSchemaActionArgs({ type: "object", properties: {
      reference: { type: "string", default: "fixed", readOnly: true },
      profile: { type: "object", properties: { count: { type: "integer" } } },
    } }, defaultWidgets, { initialValues: { profile: { count: 4 }, undeclared: true } });
    expect(args.defaultValues).toEqual({ reference: "fixed", profile: { count: 4 } });
    if (typeof args.fields !== "function") throw new Error("Expected dynamic fields");
    expect(args.fields({})[0]).toMatchObject({ name: "reference", readOnly: true });
  });

  test("validates retained readonly row constants through native prefixItems", async () => {
    const item = { type: "object", additionalProperties: false, required: ["source"], properties: {
      source: { type: "string", readOnly: true },
    } };
    const args = jsonSchemaActionArgs({ type: "object", properties: {
      rows: { type: "array", default: [{ source: "Retained" }], items: item,
        prefixItems: [{ ...item, properties: { source: { ...item.properties.source, default: "Retained", const: "Retained" } } }],
      },
    } }, defaultWidgets);
    expect((await args.resolver!(args.defaultValues!, undefined, resolverOptions)).errors).toEqual({});
    expect((await args.resolver!({ rows: [{ source: "Changed" }] }, undefined, resolverOptions)).errors)
      .toMatchObject({ rows: [{ source: { message: "Keep the supplied value." } }] });
  });

  test("accepts FormSpec annotations and returns validated values", async () => {
    const resolver = createJsonSchemaResolver({
      type: "object", required: ["title"], additionalProperties: false,
      properties: { title: { type: "string", minLength: 1, label: "Title", widget: "text" } },
    });
    const values = { title: "Example" };
    const result = await resolver(values, undefined, resolverOptions);
    expect(result).toEqual({ values, errors: {} });
    expect(result.values).not.toBe(values);
  });

  test("binds required, additional and array-child issues through native nested errors", async () => {
    const resolver = createJsonSchemaResolver({
      type: "object", required: ["title"], additionalProperties: false,
      properties: {
        title: { type: "string" },
        rows: { type: "array", items: {
          type: "object", required: ["label"], properties: {
            label: { type: "string" }, count: { type: "integer", minimum: 1 },
          },
        } },
      },
    });
    const result = await resolver({ extra: true, rows: [{ count: 0 }] }, undefined, resolverOptions);
    expect(result.values).toEqual({});
    expect(result.errors).toMatchObject({
      title: { type: "server", message: "This field is required." },
      extra: { message: "Remove this unexpected field." },
      rows: [{
        label: { message: "This field is required." },
        count: { message: "Enter a value of at least 1." },
      }],
    });
  });

  test("native discriminator reports only the selected branch", async () => {
    const resolver = createJsonSchemaResolver({
      type: "object", discriminator: { propertyName: "kind" }, required: ["kind"],
      properties: { kind: { type: "string" } },
      oneOf: [
        { properties: { kind: { const: "text" }, text: { type: "string", minLength: 3 } }, required: ["text"] },
        { properties: { kind: { const: "count" }, count: { type: "integer", minimum: 1 } }, required: ["count"] },
      ],
    });
    expect(await resolver({ kind: "text", text: "abc" }, undefined, resolverOptions)).toEqual({
      values: { kind: "text", text: "abc" }, errors: {},
    });
    const invalid = await resolver({ kind: "text", text: "a" }, undefined, resolverOptions);
    expect(Object.keys(invalid.errors)).toEqual(["text"]);
    expect(invalid.errors.text).toMatchObject({ message: "Enter at least 3 characters." });
    const unknown = await resolver({ kind: "other" }, undefined, resolverOptions);
    expect(Object.keys(unknown.errors)).toEqual(["kind"]);
  });

  test("preserves descendant issues when a later schema adds a parent issue", async () => {
    const resolver = createJsonSchemaResolver({
      type: "object", allOf: [
        { properties: { profile: { type: "object", required: ["name"], properties: { name: { type: "string" } } } } },
        { properties: { profile: { type: "object", minProperties: 2 } } },
      ],
    });
    expect((await resolver({ profile: {} }, undefined, resolverOptions)).errors).toMatchObject({
      profile: {
        message: "Enter a valid value.",
        name: { message: "This field is required." },
      },
    });
  });

  test.each([
    ["email", "reader@example.org", "not an email", "email address"],
    ["date", "2024-02-29", "2025-02-29", "date"],
    ["date-time", "2025-04-01T12:00:00Z", "2025-04-01T12:00:00", "date and time"],
  ])("asserts the native %s format with a readable name", async (format, valid, invalid, label) => {
    const resolver = createJsonSchemaResolver({
      type: "object", properties: { value: { type: "string", format } },
    });
    expect((await resolver({ value: valid }, undefined, resolverOptions)).errors).toEqual({});
    expect((await resolver({ value: invalid }, undefined, resolverOptions)).errors.value).toMatchObject({
      message: `Enter a valid ${label}.`,
    });
  });

  test("coerces validated output without mutating the draft", async () => {
    const resolver = createJsonSchemaResolver<{ count: string }, unknown, { count: number }>({
      type: "object", required: ["count"], properties: { count: { type: "integer" } },
    }, createJsonSchemaAjv({ coerceTypes: true }));
    const draft = { count: "12" };
    expect(await resolver(draft, undefined, { fields: {}, shouldUseNativeValidation: false })).toEqual({ values: { count: 12 }, errors: {} });
    expect(draft).toEqual({ count: "12" });
  });

  test("maps escaped pointer segments and root failures to the shared issue shape", () => {
    expect(ajvValidationErrors([
      { instancePath: "/a~1b/~0key", schemaPath: "#/type", keyword: "type", params: { type: "string" }, message: "must be string" },
      { instancePath: "", schemaPath: "#/not", keyword: "not", params: {}, message: "must NOT be valid" },
    ])).toEqual({ fieldErrors: { "a/b.~key": ["Enter a valid text value."] }, formErrors: ["Enter a valid value."] });
  });

  test.each([
    ["string", 4, "text value"], ["integer", 1.5, "whole number"],
    ["number", "many", "number"], ["boolean", "yes", "true or false value"],
    ["array", {}, "list"], ["object", [], "set of fields"], ["null", "filled", "empty value"],
  ])("uses a readable %s type name", async (type, value, label) => {
    const resolver = createJsonSchemaResolver({ type: "object", properties: { value: { type } } });
    expect((await resolver({ value }, undefined, resolverOptions)).errors.value).toMatchObject({
      message: `Enter a valid ${label}.`,
    });
  });

  test("localizes shared messages and type and format names using the active UI translator", async () => {
    const i18n = createAngeeI18nInstance({ ui: {
      "form.required": "Vyplňte toto pole.",
      "form.validation.type": "Zadejte {type}.",
      "form.validation.type.integer": "celé číslo",
      "form.validation.format": "Zadejte {format}.",
      "form.validation.format.email": "e-mailovou adresu",
    } });
    const resolver = createJsonSchemaResolver({
      type: "object", required: ["title"], properties: {
        title: { type: "string" }, count: { type: "integer" }, address: { type: "string", format: "email" },
      },
    }, undefined, undefined, i18n.getFixedT(null, "ui"));
    expect((await resolver({ count: 1.5, address: "bad" }, undefined, resolverOptions)).errors).toMatchObject({
      title: { message: "Vyplňte toto pole." },
      count: { message: "Zadejte celé číslo." },
      address: { message: "Zadejte e-mailovou adresu." },
    });
  });

  test("formats field and root messages using native keyword parameters", async () => {
    const resolver = createJsonSchemaResolver({
      type: "object", minProperties: 2,
      properties: { title: { type: "string", minLength: 3 } },
    }, undefined, (error) => error.keyword === "minLength"
      ? `Use at least ${String(error.params.limit)} characters.`
      : "Complete the form.");
    expect((await resolver({ title: "a" }, undefined, resolverOptions)).errors).toMatchObject({
      title: { message: "Use at least 3 characters." },
      root: { server: { message: "Complete the form." } },
    });
    expect(ajvValidationErrors([
      { instancePath: "", schemaPath: "#/required", keyword: "required", params: { missingProperty: "title" } },
    ], () => "Enter a value.")).toEqual({
      fieldErrors: { title: ["Enter a value."] }, formErrors: [],
    });
  });

  test("binds root errors and rejects unsupported async schemas at construction", async () => {
    const resolver = createJsonSchemaResolver(false);
    expect((await resolver({}, undefined, resolverOptions)).errors).toMatchObject({
      root: { server: { message: "Enter a valid value." } },
    });
    expect(() => createJsonSchemaResolver({ $async: true, type: "object" }))
      .toThrow("JSON Schema form resolvers require a synchronous schema.");
  });
});
