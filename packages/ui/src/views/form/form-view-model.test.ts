import { describe, expect, test } from "vitest";

import type { FieldDescriptor } from "../page";
import type { FormSpecFieldDescriptor } from "./form-spec";
import {
  emptyDraft,
  addFieldSelection,
  fieldErrorMessages,
  formViewFieldLayout,
  isCompositeFieldDescriptor,
  missingRequiredFieldNames,
  mutationData,
  recordToValues,
  resolveField,
  titleText,
} from "./form-view-model";

const fields: readonly FieldDescriptor[] = [
  { name: "config.local_root", widget: "text" },
  { name: "config.local_name", widget: "text" },
];

test.each<{ field: FormSpecFieldDescriptor; composite: boolean }>([
  { field: { name: "title", widget: "text" }, composite: false },
  { field: { name: "config", widget: "json" }, composite: false },
  { field: { name: "config", objectTemplate: [] }, composite: true },
  { field: { name: "tags", itemTemplate: { name: "item", widget: "text" } }, composite: true },
  { field: { name: "rows", rowTemplate: [] }, composite: true },
  { field: { name: "rows", rowTemplate: undefined }, composite: false },
])("descriptor $field owns composite controls: $composite", ({ field, composite }) => {
  expect(isCompositeFieldDescriptor(field)).toBe(composite);
});

test("field errors retain parent and nested messages while excluding RHF metadata", () => {
  expect(fieldErrorMessages([{
    message: "Choose another title.",
    type: "validate",
    types: { minLength: "Choose another title." },
    ref: { message: "DOM input details are not validation." },
    nested: { message: "Choose a valid relation." },
  }])).toEqual(["Choose another title.", "Choose a valid relation."]);
  expect(fieldErrorMessages([{
    nested: { message: "Choose a valid relation.", ref: { current: null } },
  }])).toEqual(["Choose a valid relation."]);
});

test("absent field errors leave untouched controls valid and retain actual nested errors", () => {
  expect(fieldErrorMessages([undefined, null])).toEqual([]);
  expect(fieldErrorMessages([{ lines: [undefined, null, {
    quantity: { message: "Enter a quantity." },
  }] }], "invoice")).toEqual(["invoice.lines.2.quantity: Enter a quantity."]);
  expect(fieldErrorMessages(["Required", { message: 0 }])).toEqual(["Required", "0"]);
});

test("structured errors preserve child fields named message and types", () => {
  expect(fieldErrorMessages([{
    message: { message: "Enter a message.", type: "validate" },
    types: { message: "Choose a type.", types: { required: "Choose a type." } },
  }], "config")).toEqual([
    "config.message: Enter a message.",
    "config.types: Choose a type.",
  ]);
});

test("structured field errors retain explicit root paths through objects and arrays", () => {
  const errors = [{
    tasks: [{
      relation: {
        message: "Choose a valid relation.",
        ref: { message: "DOM input details are not validation." },
      },
    }],
  }];
  expect(fieldErrorMessages(errors, "config")).toEqual(["config.tasks.0.relation: Choose a valid relation."]);
  expect(fieldErrorMessages(errors, "")).toEqual(["tasks.0.relation: Choose a valid relation."]);
});

test("titleText preserves string and numeric scalar titles", () => {
  expect(titleText("Daily briefing", "Untitled")).toBe("Daily briefing");
  expect(titleText(42, "Untitled")).toBe("42");
});

test("dynamic resolution cannot unlock a field already locked by form mode", () => {
  const locked: FieldDescriptor = {
    name: "journal",
    readOnly: true,
    resolve: () => ({ name: "journal", readOnly: false }),
  };
  expect(resolveField(locked, {})).toMatchObject({ name: "journal", readOnly: true });
  expect(resolveField({ ...locked, readOnly: false }, {})).toMatchObject({
    name: "journal", readOnly: false,
  });
});

test("keeps long text fields in declaration order when they opt out of body placement", () => {
  const addressFields: readonly FieldDescriptor[] = [
    { name: "label", title: true },
    { name: "street", widget: "textarea", body: false },
    { name: "extended", widget: "textarea", body: false },
    { name: "po_box" },
    { name: "city" },
    { name: "region" },
    { name: "postal_code" },
    { name: "country" },
    { name: "is_primary", widget: "switch" },
  ];

  const layout = formViewFieldLayout(addressFields, addressFields, [], null);

  expect(layout.bodyField).toBeUndefined();
  expect(layout.gridFields.map((field) => field.name)).toEqual([
    "street",
    "extended",
    "po_box",
    "city",
    "region",
    "postal_code",
    "country",
    "is_primary",
  ]);
});

test("object fields select only their declared row projection paths", () => {
  const paths = new Set<string>();
  addFieldSelection(
    paths,
    { name: "payload" },
    undefined,
    { name: "payload", kind: "object" },
    {
      kind: "object", scalar: null, values: [], nullable: true,
      row: { path: "payload", paths: ["payload.kind", "payload.target.id"] },
      filter: null, sort: null, relation: null,
    },
  );
  expect([...paths]).toEqual(["payload.kind", "payload.target.id"]);
});

describe("dotted form fields", () => {
  test("seed and read nested record values through native RHF-shaped data", () => {
    expect(emptyDraft(fields, { config: { local_root: "/srv/repo" } })).toEqual({
      config: { local_root: "/srv/repo", local_name: "" },
    });
    expect(recordToValues({ config: { local_root: "/repo", local_name: "main" } }, fields))
      .toEqual({ config: { local_root: "/repo", local_name: "main" } });
    expect(emptyDraft([
      { name: "config", widget: "json" },
      { name: "config.local_root", widget: "text", defaultValue: "/default" },
    ])).toEqual({ config: { local_root: "/default" } });
  });

  test("does not seed hidden implementation config before an implementation is selected", () => {
    const conditional: FieldDescriptor = {
      name: "config.local_root",
      widget: "text",
      defaultValue: "/default",
      showWhen: (values) => values.backend_class === "local",
    };
    expect(emptyDraft([
      { name: "backend_class", widget: "select" },
      { name: "config", widget: "json", showWhen: (values) => !values.backend_class },
      conditional,
    ])).toEqual({ backend_class: "", config: {} });
  });

  test("resolves visibility from the complete baseline regardless of declaration order", () => {
    expect(emptyDraft([
      {
        name: "config.local_root", widget: "text", defaultValue: "/default",
        showWhen: (values) => values.backend_class === "local",
      },
      { name: "backend_class", widget: "select", defaultValue: "local" },
    ])).toEqual({ config: { local_root: "/default" }, backend_class: "local" });
    expect(recordToValues(
      { backend_class: "local", config: { local_root: "/record" } },
      [
        { name: "config.local_root", widget: "text", showWhen: (values) => values.backend_class === "local" },
        { name: "backend_class", widget: "select" },
      ],
    )).toEqual({ config: { local_root: "/record" }, backend_class: "local" });
  });

  test("does not mutate nested caller defaults while building the evaluation baseline", () => {
    const config = Object.freeze({ local_root: "/caller" });
    const defaults = Object.freeze({ backend_class: "local", config });
    expect(emptyDraft([
      { name: "config", widget: "json" },
      { name: "config.local_name", widget: "text", defaultValue: "main" },
      { name: "backend_class", widget: "select" },
    ], defaults)).toEqual({
      config: { local_root: "/caller", local_name: "main" },
      backend_class: "local",
    });
    expect(defaults).toEqual({ backend_class: "local", config: { local_root: "/caller" } });
  });

  test("uses the selected descriptor required flag", () => {
    const dynamic: FieldDescriptor = {
      name: "config.shared",
      resolve: (values) => ({
        name: "config.shared",
        required: values.backend_class === "strict",
      }),
    };
    expect(missingRequiredFieldNames(
      { backend_class: "strict", config: { shared: "" } }, [dynamic], new Set(),
    )).toEqual(["config.shared"]);
    expect(missingRequiredFieldNames(
      { backend_class: "loose", config: { shared: "" } }, [dynamic], new Set(),
    )).toEqual([]);
  });

  test("accepts explicit null only for a nullable required descriptor", () => {
    expect(missingRequiredFieldNames(
      { note: null }, [{ name: "note", required: true, nullable: true, presenceRequired: true }], new Set(),
    )).toEqual([]);
    expect(missingRequiredFieldNames(
      { note: null }, [{ name: "note", required: true }], new Set(),
    )).toEqual(["note"]);
  });

  test("hydrates explicit null and omission without changing their presence", () => {
    const presenceFields: FieldDescriptor[] = [
      { name: "note", nullable: true, omittable: true },
      { name: "settings", kind: "object", nullable: true, omittable: true },
      { name: "summary", omittable: true },
    ];

    expect(recordToValues({ note: null, settings: null }, presenceFields)).toEqual({
      note: null,
      settings: null,
    });
  });

  test("uses JSON presence and declared constraints only when explicitly projected", () => {
    const presenceFields: FieldDescriptor[] = [
      { name: "title", required: true, presenceRequired: true },
      { name: "items", required: true, presenceRequired: true, kind: "array" },
    ];
    expect(missingRequiredFieldNames({ title: "", items: [] }, presenceFields, new Set())).toEqual([]);
    expect(missingRequiredFieldNames({ title: "" }, presenceFields, new Set())).toEqual(["items"]);
    expect(missingRequiredFieldNames(
      { title: "", items: [] }, [{ ...presenceFields[0]!, minLength: 1 }, { ...presenceFields[1]!, minItems: 1 }], new Set(),
    )).toEqual(["title", "items"]);
    expect(missingRequiredFieldNames(
      { title: "" }, [{ name: "title", required: true }], new Set(),
    )).toEqual(["title"]);
    const optional = { name: "summary", omittable: true, minLength: 2 };
    expect(missingRequiredFieldNames({}, [optional], new Set())).toEqual([]);
    expect(missingRequiredFieldNames({ summary: "" }, [optional], new Set())).toEqual(["summary"]);
  });

  test("submits nested config through its writable root and nested dirty state", () => {
    expect(mutationData(
      { config: { local_root: "/repo", local_name: "main" } },
      fields,
      {
        dirtyFields: { config: { local_root: true, local_name: true } },
        isCreate: true,
        writableFields: new Set(["config"]),
      },
    )).toEqual({ config: { local_root: "/repo", local_name: "main" } });
  });

  test("preserves visible config siblings when one nested field changes", () => {
    expect(mutationData(
      { config: { local_root: "/repo", local_name: "renamed" } },
      fields,
      {
        dirtyFields: { config: { local_name: true } },
        id: "bridge-1",
        isCreate: false,
        writableFields: new Set(["config"]),
      },
    )).toEqual({
      id: "bridge-1",
      config: { local_root: "/repo", local_name: "renamed" },
    });
  });

  test("does not submit nested config for empty or all-false dirty groups", () => {
    for (const configDirty of [{}, { local_root: false, local_name: false }]) {
      expect(mutationData(
        { config: { local_root: "/repo", local_name: "main" } }, fields,
        {
          dirtyFields: { config: configDirty }, id: "bridge-1", isCreate: false,
          writableFields: new Set(["config"]),
        },
      )).toEqual({ id: "bridge-1" });
    }
  });

  test("submits cleared numeric edits as null without inventing zero", () => {
    expect(mutationData(
      { count: "", ratio: "" },
      [
        { name: "count", widget: "integer" },
        { name: "ratio", widget: "float" },
      ],
      {
        dirtyFields: { count: true, ratio: true },
        id: "record-1",
        isCreate: false,
      },
    )).toEqual({ id: "record-1", count: null, ratio: null });
  });
});
