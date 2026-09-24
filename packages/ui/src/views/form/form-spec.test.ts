import { describe, expect, test } from "vitest";

import { defaultWidgets } from "../../widgets";
import { FORM_SPEC_ANNOTATIONS } from "./form-spec-schema";
import {
  deserializeFormSpec,
  formSpecInitialValues,
  normalizeFormSpecValues,
} from "./form-spec";

test("FormSpec registers only its presentation annotations with JSON Schema validators", () => {
  expect(FORM_SPEC_ANNOTATIONS).toEqual([
    "propertyOrder", "widget", "label", "addLabel", "removeLabel", "placeholder",
    "hidden", "layout", "omittable", "presenceRequired", "defaultValue", "options", "relation",
  ]);
});

describe("deserializeFormSpec", () => {
  test("retains hidden schema fields through initialization and normalized submission", () => {
    const fields = deserializeFormSpec({
      type: "object",
      properties: {
        identity: { type: "string", hidden: true, readOnly: true },
        details: { type: "object", widget: "object", properties: {
          fingerprint: { type: "string", hidden: true },
          title: { type: "string" },
        } },
        items: { type: "array", widget: "list", items: {
          type: "object", widget: "object", properties: {
            identity: { type: "string", hidden: true },
            title: { type: "string" },
          },
        } },
      },
    }, defaultWidgets);
    const payload = {
      identity: "retained-root",
      details: { fingerprint: "retained-fingerprint", title: "Details" },
      items: [{ identity: "retained-item", title: "Item" }],
    };

    expect(fields[0]).toMatchObject({ hidden: true, readOnly: true });
    expect(fields[1]?.objectTemplate?.[0]).toMatchObject({ hidden: true });
    expect(fields[2]?.itemTemplate?.objectTemplate?.[0]).toMatchObject({ hidden: true });
    const initialValues = formSpecInitialValues(fields, payload);
    expect(initialValues).toEqual(payload);
    expect(normalizeFormSpecValues(fields, initialValues)).toEqual(payload);
  });

  test.each(["$defs", "definitions"])("resolves %s references for rows, objects, and list items without losing annotations", (definitions) => {
    const ref = (name: string) => ({ $ref: `#/${definitions}/${name}` });
    const schema = {
      type: "object",
      [definitions]: {
        Identity: { type: "string", enum: ["first", "second"], label: "Default identity" },
        Choice: {
          type: "object", required: ["identity", "reason"],
          propertyOrder: ["identity", "reason"],
          properties: {
            reason: { type: "string", label: "Reason", minLength: 1 },
            identity: {
              ...ref("Identity"), label: "Identity", default: "first",
              options: [{ value: "first", label: "First identity" }, { value: "second", label: "Second identity" }],
            },
          },
        },
        Alias: ref("Choice"),
      },
      properties: {
        choices: { type: "array", items: ref("Alias") },
        editable: { type: "array", widget: "list", items: { ...ref("Choice"), widget: "object" } },
        choice: { ...ref("Choice"), widget: "object" },
        identity: ref("Identity"),
      },
    };
    const original = structuredClone(schema);
    const fields = deserializeFormSpec(schema, defaultWidgets);
    const columns = [
      {
        name: "identity", kind: "string", widget: "select", label: "Identity", required: true,
        defaultValue: "first", hasDefault: true,
        options: [{ value: "first", label: "First identity" }, { value: "second", label: "Second identity" }],
      },
      { name: "reason", kind: "string", widget: "text", label: "Reason", required: true, minLength: 1 },
    ];

    expect(fields[0]).toEqual({ name: "choices", kind: "array", widget: "rows", rowTemplate: columns });
    expect(fields[1]?.itemTemplate?.objectTemplate).toEqual(columns);
    expect(fields[2]?.objectTemplate).toEqual(columns);
    expect(fields[3]).toMatchObject({ label: "Default identity", options: [
      { value: "first", label: "first" }, { value: "second", label: "second" },
    ] });
    expect(schema).toEqual(original);
  });

  test.each(["#/$defs/Missing", "#/definitions/Missing", "https://example.test/schema"])(
    "rejects an unresolved item reference %s", ($ref) => {
      expect(() => deserializeFormSpec({ properties: {
        choices: { type: "array", items: { $ref } },
      } }, defaultWidgets)).toThrow(/Invalid .*choices.*reference/i);
    },
  );

  test("decodes escaped definition names", () => {
    expect(deserializeFormSpec({
      $defs: { "Identity/with~space ": { type: "string" } },
      properties: { identity: { $ref: "#/$defs/Identity~1with~0space%20" } },
    }, defaultWidgets)).toEqual([{ name: "identity", kind: "string", widget: "text" }]);
  });

  test.each([
    { Choice: { $ref: "#/$defs/Choice" } },
    { Choice: { $ref: "#/$defs/Other" }, Other: { $ref: "#/$defs/Choice" } },
    { Choice: { type: "object", widget: "object", properties: { children: { type: "array", items: { $ref: "#/$defs/Choice" } } } } },
    { Choice: { type: "array", widget: "list", items: { $ref: "#/$defs/Choice" } } },
  ])("rejects cyclic references instead of recursing indefinitely", ($defs) => {
    expect(() => deserializeFormSpec({ $defs, properties: {
      choices: { type: "array", widget: "list", items: { $ref: "#/$defs/Choice" } },
    } }, defaultWidgets)).toThrow(/Invalid .*choices.*cyclic reference/i);
  });

  test.each([{ widget: "json" }, { layout: "context", widget: "object" }])(
    "leaves a recursive property opaque when its widget owns the value", (presentation) => {
      const fields = deserializeFormSpec({
        $defs: { Node: { type: "object", properties: {
          name: { type: "string" }, parent: { $ref: "#/$defs/Node", ...presentation },
        } } },
        properties: { nodes: { type: "array", items: { $ref: "#/$defs/Node" } } },
      }, defaultWidgets);
      expect(fields[0]?.rowTemplate).toEqual([
        { name: "name", kind: "string", widget: "text" },
        { name: "parent", kind: "object", ...presentation },
      ]);
    },
  );

  test("uses retained property order after persisted nested properties are reordered", () => {
    const fields = deserializeFormSpec({
      type: "object",
      propertyOrder: ["invoice", "note"],
      properties: {
        note: { type: "string" },
        invoice: {
          type: "object", widget: "object",
          propertyOrder: ["supplier", "reference", "lines"],
          properties: {
            lines: {
              type: "array", widget: "list",
              items: {
                type: "object", widget: "object",
                propertyOrder: ["description", "quantity"],
                properties: {
                  quantity: { type: "number" },
                  description: { type: "string" },
                },
              },
            },
            reference: { type: "string" },
            supplier: { type: "string" },
          },
        },
      },
    }, defaultWidgets);

    expect(fields.map((field) => field.name)).toEqual(["invoice", "note"]);
    expect(fields[0]?.objectTemplate?.map((field) => field.name))
      .toEqual(["supplier", "reference", "lines"]);
    expect(fields[0]?.objectTemplate?.[2]?.itemTemplate?.objectTemplate?.map((field) => field.name))
      .toEqual(["description", "quantity"]);
  });

  test("maps the recursive backend schema and its data-only UI extensions", () => {
    const fields = deserializeFormSpec(
      {
        type: "object",
        required: ["title", "target", "rows"],
        properties: {
          title: {
            type: "string",
            label: "Title",
            description: "Human-readable title",
            placeholder: "Review import",
            defaultValue: "Untitled",
          },
          count: { type: "integer" },
          confidence: { type: "number" },
          approved: { type: "boolean" },
          config: { type: "object" },
          tags: { type: "array", items: { type: "string" } },
          mode: {
            enum: ["append", "replace"],
            options: [
              { value: "append", label: "Append" },
              { value: "replace", label: "Replace", disabled: true },
            ],
          },
          target: {
            type: "string",
            relation: {
              resource: "Channel",
              labelField: "name",
              filters: [{ field: "status", operator: "eq", value: "active" }],
              create: {
                resource: "Channel",
                defaultValues: { parent_id: "parent_7", revision: 3 },
                actionLabel: "Create channel",
                title: "Create channel",
              },
            },
          },
          rows: {
            type: "array",
            items: {
              type: "object",
              required: ["target"],
              properties: {
                target: {
                  type: "string",
                  relation: { resource: "Channel" },
                },
                replace: { type: "boolean", widget: "switch" },
              },
            },
          },
        },
      },
      defaultWidgets,
    );

    expect(fields).toEqual([
      {
        name: "title",
        kind: "string",
        widget: "text",
        label: "Title",
        description: "Human-readable title",
        placeholder: "Review import",
        required: true,
        defaultValue: "Untitled",
        hasDefault: true,
      },
      { name: "count", kind: "integer", widget: "integer" },
      { name: "confidence", kind: "number", widget: "float" },
      { name: "approved", kind: "boolean", widget: "boolean" },
      { name: "config", kind: "object", widget: "json" },
      { name: "tags", kind: "array", widget: "json" },
      {
        name: "mode",
        kind: "any",
        widget: "select",
        options: [
          { value: "append", label: "Append" },
          { value: "replace", label: "Replace", disabled: true },
        ],
      },
      {
        name: "target",
        kind: "string",
        widget: "many2one",
        required: true,
        relation: {
          resource: "Channel",
          labelField: "name",
          filters: [{ field: "status", operator: "eq", value: "active" }],
          create: {
            resource: "Channel",
            defaultValues: { parent_id: "parent_7", revision: 3 },
            actionLabel: "Create channel",
            title: "Create channel",
          },
        },
      },
      {
        name: "rows",
        kind: "array",
        widget: "rows",
        required: true,
        rowTemplate: [
          {
            name: "target",
            kind: "string",
            widget: "many2one",
            required: true,
            relation: { resource: "Channel" },
          },
          {
            name: "replace",
            kind: "boolean",
            widget: "switch",
          },
        ],
      },
    ]);
  });

  test("derives select options from an enum", () => {
    expect(
      deserializeFormSpec(
        {
          type: "object",
          properties: { mode: { enum: ["append", "replace"] } },
        },
        defaultWidgets,
      ),
    ).toEqual([
      {
        name: "mode",
        kind: "any",
        widget: "select",
        options: [
          { value: "append", label: "append" },
          { value: "replace", label: "replace" },
        ],
      },
    ]);
  });

  test("preserves an empty root JSON Pointer as an authored select value", () => {
    const fields = deserializeFormSpec({
      type: "object",
      properties: {
        selector: {
          type: "string",
          options: [{ value: "", label: "Root document" }],
        },
      },
    }, defaultWidgets);

    expect(fields).toEqual([{
      name: "selector",
      kind: "string",
      widget: "select",
      options: [{ value: "", label: "Root document" }],
    }]);
    expect(formSpecInitialValues(fields, { selector: "" })).toEqual({ selector: "" });
    expect(normalizeFormSpecValues(fields, { selector: "" })).toEqual({ selector: "" });
  });

  test("retains the explicit approval layout annotation", () => {
    expect(deserializeFormSpec({ properties: {
      source: { type: "string", layout: "context" },
      action: { type: "string", layout: "input" },
    } }, defaultWidgets)).toEqual([
      { name: "source", kind: "string", widget: "text", layout: "context" },
      { name: "action", kind: "string", widget: "text", layout: "input" },
    ]);
    expect(() => deserializeFormSpec({ properties: {
      source: { type: "string", layout: "summary" },
    } }, defaultWidgets)).toThrow(/Invalid source.layout/);
  });

  test("rejects enum values the string-valued select cannot preserve", () => {
    expect(() =>
      deserializeFormSpec(
        {
          type: "object",
          properties: { priority: { enum: [1, 2] } },
        },
        defaultWidgets,
      ),
    ).toThrowError(
      "Invalid priority.enum.0: form-spec select values must be strings.",
    );
  });

  test("throws instead of silently falling back for an unknown widget", () => {
    expect(() =>
      deserializeFormSpec(
        {
          type: "object",
          properties: { summary: { type: "string", widget: "missing" } },
        },
        defaultWidgets,
      ),
    ).toThrowError(
      'Unknown form spec widget "missing" for field "summary". Register it in AppRuntime.widgets.',
    );
  });

  test("rejects an unknown Refine relation-filter operator", () => {
    expect(() =>
      deserializeFormSpec(
        {
          type: "object",
          properties: {
            target: {
              type: "string",
              relation: {
                resource: "Channel",
                filters: [
                  {
                    field: "status",
                    operator: "approximately",
                    value: "active",
                  },
                ],
              },
            },
          },
        },
        defaultWidgets,
      ),
    ).toThrowError(
      'Invalid target.relation.filters.0.operator: unknown Refine CRUD operator "approximately".',
    );
  });
});

describe("formSpecInitialValues", () => {
  test("honors retained JSON Schema defaults without replacing explicit payload or presentation defaults", () => {
    const fields = deserializeFormSpec({ type: "object", properties: {
      action: { type: "string", enum: ["keep", "replace"], default: "keep" },
      enabled: { type: "boolean", default: false },
      optional: { type: "string", nullable: true, default: "source", defaultValue: null },
    } }, defaultWidgets);
    expect(formSpecInitialValues(fields, {})).toEqual({ action: "keep", enabled: false, optional: null });
    expect(formSpecInitialValues(fields, { action: "replace" }).action).toBe("replace");
  });

  test("prefills declared fields from payload before schema defaults", () => {
    const fields = deserializeFormSpec(
      {
        type: "object",
        properties: {
          title: { type: "string", defaultValue: "Untitled" },
          approved: { type: "boolean" },
          note: { type: "string", defaultValue: "Review carefully" },
          rows: {
            type: "array",
            items: { type: "object", properties: {} },
          },
        },
      },
      defaultWidgets,
    );

    expect(
      formSpecInitialValues(fields, {
        title: "Import contacts",
        approved: true,
        rows: [{ target: "chn_1" }],
      }),
    ).toEqual({
      title: "Import contacts",
      approved: true,
      note: "Review carefully",
      rows: [{ target: "chn_1" }],
    });
  });

  test("preserves an opaque recursive context for its owning renderer", () => {
    const fields = deserializeFormSpec({ $defs: {
      Context: { type: "object", properties: {
        children: { type: "array", items: { $ref: "#/$defs/Context" } },
      } },
    }, properties: {
      review_context: {
        $ref: "#/$defs/Context", layout: "context", readOnly: true, widget: "object",
      },
      contexts: {
        type: "array", items: { $ref: "#/$defs/Context" }, layout: "context", widget: "json",
      },
    } }, { ...defaultWidgets, object: { read: () => null } });
    const reviewContext = {
      kind: "supplier_confirmation",
      subject: { invoice_id: "inv_1" },
      candidates: [{ party_id: "pty_1" }],
    };

    const payload = { review_context: reviewContext, contexts: [reviewContext] };
    expect(formSpecInitialValues(fields, payload)).toEqual(payload);
    expect(fields.every((field) => !field.objectTemplate && !field.rowTemplate && !field.itemTemplate)).toBe(true);
  });

  test("preserves omitted, defaulted, nullable, and falsey JSON values", () => {
    const fields = deserializeFormSpec({ properties: {
      absent: { type: "string", omittable: true },
      defaultNull: { type: "string", nullable: true, omittable: true, defaultValue: null },
      requiredNull: { type: "string", nullable: true },
      empty: { type: "string", omittable: true },
      zero: { type: "integer", omittable: true },
      disabled: { type: "boolean", omittable: true },
    } }, defaultWidgets);

    const values = formSpecInitialValues(fields, { empty: "", zero: 0, disabled: false });
    expect(values).toEqual({ defaultNull: null, requiredNull: null, empty: "", zero: 0, disabled: false });
    expect(normalizeFormSpecValues(fields, { ...values, absent: undefined })).toEqual(values);
  });

  test("accepts a JSON Schema null alternative and preserves its null default", () => {
    const fields = deserializeFormSpec({ properties: {
      reference: {
        anyOf: [{ type: "string" }, { type: "null" }],
      },
      line_total: {
        anyOf: [
          { type: "number" },
          { type: "string", pattern: "^[0-9.]+$" },
          { type: "null" },
        ],
        widget: "float",
        omittable: true,
        default: null,
      },
    } }, defaultWidgets);

    expect(fields[0]).toMatchObject({
      name: "reference", kind: "string", widget: "text", nullable: true,
    });
    expect(fields[1]).toMatchObject({ name: "line_total", nullable: true });
    expect(formSpecInitialValues(fields, {})).toEqual({ reference: null, line_total: null });
  });

  test("falls back to schema defaults when retained payload types are incompatible", () => {
    const fields = deserializeFormSpec({ properties: {
      printedTerms: {
        type: "string",
        readOnly: true,
        defaultValue: "Due 2026-09-01",
      },
      count: { type: "integer", defaultValue: 0 },
    } }, defaultWidgets);

    expect(formSpecInitialValues(fields, {
      printedTerms: { due_date: "2026-09-01" },
      count: "one",
    })).toEqual({ printedTerms: "Due 2026-09-01", count: 0 });
  });
});


describe("form-spec runtime boundary", () => {
  test("validates filters recursively before producing descriptors", () => {
    expect(() => deserializeFormSpec({ properties: {
      target: { relation: { resource: "Channel", filters: [{ operator: "and", value: [{ operator: "eq", field: 42, value: "active" }] }] } },
    } }, defaultWidgets)).toThrow(/target\.relation\.filters\.0\.value\.0\.field/);
  });

  test("rejects malformed nested rows and executable defaults", () => {
    expect(() => deserializeFormSpec({ properties: {
      lines: { type: "array", items: { type: "object", properties: { title: { readOnly: "yes" } } } },
    } }, defaultWidgets)).toThrow(/lines\.items\.title\.readOnly/);
    expect(() => deserializeFormSpec({ properties: { title: { defaultValue: () => "unsafe" } } }, defaultWidgets))
      .toThrow(/title\.defaultValue/);
  });
});
