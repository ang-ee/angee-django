import { describe, expect, test } from "vitest";

import { defaultWidgets } from "../../widgets";
import { FORM_SPEC_ANNOTATIONS } from "./form-spec-schema";
import {
  deserializeFormSpec,
  formSpecInitialValues,
  formSpecHasControlForPath,
  normalizeFormSpecValues,
} from "./form-spec";

test("FormSpec registers only its presentation annotations with JSON Schema validators", () => {
  expect(FORM_SPEC_ANNOTATIONS).toEqual([
    "assignmentSubjectKinds", "propertyOrder", "widget", "label", "addLabel", "removeLabel", "placeholder",
    "hidden", "layout", "omittable", "presenceRequired", "defaultValue", "options", "relation",
  ]);
});

test("control-path checks reject row errors and apply the renderer's per-row visibility rule", () => {
  const fields = [{ name: "rows", widget: "rows", rowTemplate: [
    { name: "target", showWhen: (row: Record<string, unknown>) => row.enabled === true },
    { name: "hidden", hidden: true },
  ] }];
  const values = { rows: [{ enabled: false }, { enabled: true }] };
  expect(formSpecHasControlForPath(fields, "rows.0", values)).toBe(false);
  expect(formSpecHasControlForPath(fields, "rows.0.target", values)).toBe(false);
  expect(formSpecHasControlForPath(fields, "rows.1.target", values)).toBe(true);
  expect(formSpecHasControlForPath(fields, "rows.1.hidden", values)).toBe(false);
  expect(formSpecHasControlForPath(fields, "rows.2.target", values)).toBe(false);
});

test("invalid subject-kind annotations fail at the wire owner", () => {
  expect(() => deserializeFormSpec({ properties: { people: {
    type: "array", widget: "json", assignmentSubjectKinds: ["uesr"], items: { type: "string" },
  } } }, defaultWidgets)).toThrow("assignmentSubjectKinds");
});

describe("deserializeFormSpec", () => {
  test("only rendered controls claim nested issue paths while atomic widgets retain child messages", () => {
    const fields = deserializeFormSpec({ type: "object", properties: {
      visible: { type: "string" }, hidden: { type: "string", hidden: true },
      details: { type: "object", properties: { title: { type: "string" } } },
      rows: { type: "array", widget: "list", items: { type: "object", properties: { title: { type: "string" } } } },
      raw: { type: "object", widget: "json" },
    } }, defaultWidgets);
    for (const path of ["visible", "details.title", "rows.0.title", "raw.anything"])
      expect(formSpecHasControlForPath(fields, path, { rows: [{ title: "" }] }), path).toBe(true);
    for (const path of ["hidden", "unknown", "details.unknown", "rows.0.unknown"])
      expect(formSpecHasControlForPath(fields, path, { rows: [{ title: "" }] }), path).toBe(false);
  });

  test("custom subject arrays carry typed widget options without becoming composite fields", () => {
    const fields = deserializeFormSpec({ properties: { recipients: {
      type: "array", widget: "recipients", assignmentSubjectKinds: ["user"], items: { type: "string" },
    } } }, { ...defaultWidgets, recipients: defaultWidgets.text! });
    expect(fields[0]).toMatchObject({ widget: "recipients", assignmentSubjectKinds: ["user"] });
    expect(fields[0]?.itemTemplate).toBeUndefined();
  });

  test("projects declared objects through nested fields while keeping explicit JSON opaque", () => {
    const schema = { type: "object", properties: {
      identity: { type: "string", title: "Identity", readOnly: true, default: "retained" },
      note: { type: "string", title: "Note" },
    } };
    const [structured, opaque] = deserializeFormSpec({ properties: {
      details: schema, raw: { ...schema, widget: "json" },
    } }, defaultWidgets);
    expect(structured).toMatchObject({ widget: "object", objectTemplate: [
      { name: "identity", readOnly: true, defaultValue: "retained" },
      { name: "note", label: "Note" },
    ] });
    expect(opaque).toMatchObject({ widget: "json" });
    expect(opaque?.objectTemplate).toBeUndefined();
  });

  test("projects nullable object children with their immutable defaults and outer annotations", () => {
    const fields = deserializeFormSpec({ properties: {
      details: {
        title: "Retained details", description: "Review the details", default: null,
        anyOf: [{ type: "object", title: "Inner title", required: ["identity"], properties: {
          identity: { type: "string", readOnly: true, default: "retained", const: "retained" },
          note: { type: "string", minLength: 3 },
        } }, { type: "null" }],
      },
    } }, defaultWidgets);
    expect(fields[0]).toMatchObject({
      widget: "object", nullable: true, label: "Retained details", description: "Review the details",
      defaultValue: null, objectTemplate: [
        { name: "identity", readOnly: true, required: true, defaultValue: "retained" },
        { name: "note", minLength: 3 },
      ],
    });
    expect(formSpecInitialValues(fields, {})).toEqual({ details: null });
    expect(formSpecInitialValues(fields, { details: { note: "Updated" } })).toEqual({
      details: { identity: "retained", note: "Updated" },
    });
    expect(normalizeFormSpecValues(fields, { details: null })).toEqual({ details: null });
  });

  test("projects nullable object lists through references without losing their item controls", () => {
    const schema = { $defs: {
      Entries: { type: "array", minItems: 1, items: { type: "object", properties: {
        identity: { type: "string", readOnly: true, default: "retained" },
        note: { type: "string", title: "Note" },
      } } },
    }, properties: {
      entries: { anyOf: [{ $ref: "#/$defs/Entries" }, { type: "null" }],
        title: "Entries", widget: "list", default: [{ identity: "retained", note: "Initial" }] },
    } };
    const original = structuredClone(schema);
    const fields = deserializeFormSpec(schema, defaultWidgets);
    expect(fields[0]).toMatchObject({ widget: "list", nullable: true, label: "Entries", minItems: 1,
      itemTemplate: { widget: "object", objectTemplate: [
        { name: "identity", readOnly: true, defaultValue: "retained" },
        { name: "note", label: "Note" },
      ] },
    });
    const initial = formSpecInitialValues(fields, {});
    expect(initial).toEqual({ entries: [{ identity: "retained", note: "Initial" }] });
    expect(normalizeFormSpecValues(fields, initial)).toEqual(initial);
    expect(formSpecInitialValues(fields, { entries: null })).toEqual({ entries: null });
    expect(schema).toEqual(original);
  });

  test("projects choices inside nullable alternatives and preserves their JSON value types", () => {
    const fields = deserializeFormSpec({ properties: {
      choice: { anyOf: [{ type: "integer", enum: [1, 2], default: 1 }, { type: "null" }],
        title: "Choice", default: null },
      state: { anyOf: [{ type: "string", enum: ["first", "second"] }, { type: "null" }],
        options: [{ value: "second", label: "Second choice" }] },
    } }, defaultWidgets);
    expect(fields[0]).toMatchObject({ widget: "select", nullable: true, label: "Choice", defaultValue: null,
      options: [{ value: "0", label: "1" }, { value: "1", label: "2" }],
    });
    expect(fields[0]?.valueCodec?.fromControl("1")).toBe(2);
    expect(fields[0]?.valueCodec?.toControl(1)).toBe("0");
    expect(fields[1]).toMatchObject({ widget: "select", nullable: true,
      options: [{ value: "second", label: "Second choice" }],
    });
  });

  test("does not choose between multiple non-null alternatives", () => {
    expect(deserializeFormSpec({ properties: {
      choice: { anyOf: [
        { type: "string", enum: ["first"] }, { type: "string", enum: ["second"] }, { type: "null" },
      ] },
    } }, defaultWidgets)).toEqual([{ name: "choice", kind: "string", widget: "text", label: "Choice", nullable: true }]);
  });

  test("uses JSON Schema titles for labels while preserving explicit label precedence", () => {
    expect(deserializeFormSpec({ properties: {
      subject: { type: "string", title: "Subject" },
      note: { type: "string", title: "Schema title", label: "Authored label" },
    } }, defaultWidgets)).toEqual([
      { name: "subject", kind: "string", widget: "text", label: "Subject" },
      { name: "note", kind: "string", widget: "text", label: "Authored label" },
    ]);
  });

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

    expect(fields[0]).toEqual({ name: "choices", kind: "array", widget: "rows", label: "Choices", rowTemplate: columns });
    expect(fields[1]?.itemTemplate?.objectTemplate).toEqual(columns);
    expect(fields[2]?.objectTemplate).toEqual(columns);
    expect(fields[3]).toMatchObject({ label: "Default identity", options: [
          { value: "first", label: "First" }, { value: "second", label: "Second" },
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
    }, defaultWidgets)).toEqual([{ name: "identity", kind: "string", widget: "text", label: "Identity" }]);
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
        { name: "name", kind: "string", widget: "text", label: "Name" },
        { name: "parent", kind: "object", label: "Parent", ...presentation },
      ]);
    },
  );

  test("uses retained property order after persisted nested properties are reordered", () => {
    const fields = deserializeFormSpec({
      type: "object",
      propertyOrder: ["document", "note"],
      properties: {
        note: { type: "string" },
        document: {
          type: "object", widget: "object",
          propertyOrder: ["counterparty", "reference", "lines"],
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
            counterparty: { type: "string" },
          },
        },
      },
    }, defaultWidgets);

    expect(fields.map((field) => field.name)).toEqual(["document", "note"]);
    expect(fields[0]?.objectTemplate?.map((field) => field.name))
      .toEqual(["counterparty", "reference", "lines"]);
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
            title: "Target",
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
                  title: "Target",
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
      { name: "count", kind: "integer", widget: "integer", label: "Count" },
      { name: "confidence", kind: "number", widget: "float", label: "Confidence" },
      { name: "approved", kind: "boolean", widget: "boolean", label: "Approved" },
      { name: "config", kind: "object", widget: "json", label: "Config" },
      { name: "tags", kind: "array", widget: "json", label: "Tags" },
      {
        name: "mode",
        kind: "any",
        widget: "select",
        label: "Mode",
        options: [
          { value: "append", label: "Append" },
          { value: "replace", label: "Replace", disabled: true },
        ],
      },
      {
        name: "target", label: "Target",
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
        label: "Rows",
        required: true,
        rowTemplate: [
          {
            name: "target", label: "Target",
            kind: "string",
            widget: "many2one",
            required: true,
            relation: { resource: "Channel" },
          },
          {
            name: "replace",
            kind: "boolean",
            widget: "switch",
            label: "Replace",
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
        label: "Mode",
        options: [
          { value: "append", label: "Append" },
          { value: "replace", label: "Replace" },
        ],
      },
    ]);
  });

  test("uses per-value titles and humanized fallback labels for oneOf choices", () => {
    const [field] = deserializeFormSpec({ properties: {
      choice: { type: "string", oneOf: [
        { const: "retain", title: "Keep current" },
        { const: "add_more" },
      ] },
    } }, defaultWidgets);
    expect(field).toMatchObject({ widget: "select", options: [
      { value: "retain", label: "Keep current" },
      { value: "add_more", label: "Add More" },
    ] });
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
      label: "Selector",
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
      { name: "source", kind: "string", widget: "text", label: "Source", layout: "context" },
      { name: "action", kind: "string", widget: "text", label: "Action", layout: "input" },
    ]);
    expect(() => deserializeFormSpec({ properties: {
      source: { type: "string", layout: "summary" },
    } }, defaultWidgets)).toThrow(/Invalid source.layout/);
  });

  test("keeps typed JSON choices distinct through stable control tokens", () => {
    const values = [0, 1, "1", false, null, { first: 1, second: 2 }];
    const field = deserializeFormSpec({ properties: { choice: { enum: values } } }, defaultWidgets)[0]!;
    expect(field.options?.map(({ label }) => label)).toEqual(["0", "1", "1", "false", "null", '{"first":1,"second":2}']);
    for (const [index, value] of values.entries()) {
      const token = field.valueCodec?.toControl(value);
      expect(token).toBe(String(index));
      expect(field.valueCodec?.fromControl(token)).toEqual(value);
    }
    expect(field.valueCodec?.toControl({ second: 2, first: 1 })).toBe("5");
    expect(field.valueCodec?.toControl(undefined)).toBeUndefined();
    expect(field.valueCodec?.fromControl("unknown")).toBeUndefined();
    expect(field.valueCodec?.fromControl("5")).not.toBe(values[5]);
  });

  test("retains numeric values for an explicitly authored numeric widget", () => {
    const [field] = deserializeFormSpec({ properties: {
      count: { type: "integer", widget: "integer", enum: [0, 1] },
    } }, defaultWidgets);
    expect(field).toMatchObject({ widget: "integer" });
    expect(field?.valueCodec).toBeUndefined();
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

  test("rejects a registered widget that does not accept object rows", () => {
    expect(() => deserializeFormSpec({ properties: {
      choices: { type: "array", widget: "text", items: { type: "object", properties: {
        label: { type: "string" },
      } } },
    } }, defaultWidgets)).toThrowError(
      'Invalid form spec field "choices": widget "text" does not accept a row template.',
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
      kind: "counterparty_confirmation",
      subject: { document_id: "doc_1" },
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
