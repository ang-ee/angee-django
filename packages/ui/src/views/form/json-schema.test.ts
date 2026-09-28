import { describe, expect, test } from "vitest";
import type { ResolverOptions } from "react-hook-form";

import { ajvValidationErrors, createJsonSchemaAjv, createJsonSchemaResolver } from "./json-schema";

const resolverOptions: ResolverOptions<Record<string, unknown>> = {
  fields: {}, shouldUseNativeValidation: false,
};

describe("JSON Schema forms", () => {
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
      title: { type: "server", message: "must have required property 'title'" },
      extra: { message: "must NOT have additional properties" },
      rows: [{
        label: { message: "must have required property 'label'" },
        count: { message: "must be >= 1" },
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
    expect(invalid.errors.text).toMatchObject({ message: "must NOT have fewer than 3 characters" });
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
        message: "must NOT have fewer than 2 properties",
        name: { message: "must have required property 'name'" },
      },
    });
  });

  test.each([
    ["email", "reader@example.org", "not an email"],
    ["date", "2024-02-29", "2025-02-29"],
    ["date-time", "2025-04-01T12:00:00Z", "2025-04-01T12:00:00"],
  ])("asserts the native %s format", async (format, valid, invalid) => {
    const resolver = createJsonSchemaResolver({
      type: "object", properties: { value: { type: "string", format } },
    });
    expect((await resolver({ value: valid }, undefined, resolverOptions)).errors).toEqual({});
    expect((await resolver({ value: invalid }, undefined, resolverOptions)).errors.value).toMatchObject({
      message: `must match format "${format}"`,
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
    ])).toEqual({ fieldErrors: { "a/b.~key": ["must be string"] }, formErrors: ["must NOT be valid"] });
  });

  test("binds root errors and rejects unsupported async schemas at construction", async () => {
    const resolver = createJsonSchemaResolver(false);
    expect((await resolver({}, undefined, resolverOptions)).errors).toMatchObject({
      root: { server: { message: "boolean schema is false" } },
    });
    expect(() => createJsonSchemaResolver({ $async: true, type: "object" }))
      .toThrow("JSON Schema form resolvers require a synchronous schema.");
  });
});
