import { get, set } from "react-hook-form";
import { describe, expect, expectTypeOf, test } from "vitest";

import { enUiBundle } from "../../i18n";
import { createAngeeI18nInstance } from "../../runtime/i18n";
import type { FieldDescriptor } from "../page";
import type { DescriptorField } from "./DescriptorFieldList";
import type { FormSpecFieldDescriptor } from "./form-spec";
import {
  EDITABLE_LINES_SECTION,
  emptyDraft,
  addFieldSelection,
  fieldErrorMessages,
  formRuleErrors,
  formSections,
  formViewFieldLayout,
  isCompositeFieldDescriptor,
  mutationData,
  createPresetMemory,
  recordToValues,
  relationListValue,
  resolveField,
  seedPreset,
  titleText,
  visibleFields,
  withModeLockedFields,
  type PresetMemory,
} from "./form-view-model";
import { dialogRuleErrors } from "./MutationDialog";

const t = createAngeeI18nInstance(enUiBundle).getFixedT(null, "ui");
const required = { type: "required", message: "This field is required." };

const fields: readonly FieldDescriptor[] = [
  { name: "config.local_root", widget: "text" },
  { name: "config.local_name", widget: "text" },
];

test("untouched optional strings leave create defaults to the backend", () => {
  expect(mutationData(
    { title: "", color: "", description: "" },
    [{ name: "title" }, { name: "color", widget: "color" }, { name: "description" }],
    {
      dirtyFields: {}, isCreate: true,
      fieldMetadata: {
        title: { name: "title", kind: "scalar", scalar: "String", requiredOnCreate: true },
        color: { name: "color", kind: "scalar", scalar: "String", requiredOnCreate: false },
        description: { name: "description", kind: "scalar", scalar: "String", requiredOnCreate: false },
      },
    },
  )).toEqual({ title: "" });
});

test.each([
  { label: "an explicit clear", field: { name: "color" }, dirtyFields: { color: true } },
  { label: "a descriptor default", field: { name: "color", defaultValue: "" }, dirtyFields: {} },
  { label: "a supplied default", field: { name: "color" }, dirtyFields: {}, seededFieldNames: new Set(["color"]) },
  { label: "required presence", field: { name: "color", presenceRequired: true }, dirtyFields: {} },
])("create preserves $label as a blank string", ({ field, dirtyFields, seededFieldNames }) => {
  expect(mutationData({ color: "" }, [field], {
    dirtyFields, seededFieldNames, isCreate: true,
    fieldMetadata: { color: { name: "color", kind: "scalar", scalar: "String", requiredOnCreate: false } },
  })).toEqual({ color: "" });
});

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
  }] }], "document")).toEqual(["document.lines.2.quantity: Enter a quantity."]);
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
    name: "collection",
    readOnly: true,
    resolve: () => ({ readOnly: false }),
  };
  expect(resolveField(locked, {})).toMatchObject({ name: "collection", readOnly: true });
  expect(resolveField({ ...locked, readOnly: false }, {})).toMatchObject({
    name: "collection", readOnly: false,
  });
});

test("a resolve that returns a name does not compile, and the field keeps its declared name", () => {
  const field: FieldDescriptor = {
    name: "collection",
    // @ts-expect-error A `resolve` cannot rename its field, so the compiler refuses a returned `name`.
    resolve: () => ({ name: "elsewhere", readOnly: true }),
  };
  expect(resolveField(field, {})).toMatchObject({ name: "collection", readOnly: true });
});

test("a dialog field's resolve may return only dialog members, and never a name", () => {
  const relationOnly: DescriptorField = { name: "account", resolve: () => ({ relation: { resource: "Account" } }) };
  const renamed: DescriptorField = {
    name: "account",
    // @ts-expect-error A `resolve` cannot rename its field, so the compiler refuses a returned `name`.
    resolve: () => ({ name: "elsewhere", relation: { resource: "Account" } }),
  };
  expect(resolveField(relationOnly, {})).toMatchObject({ name: "account", relation: { resource: "Account" } });
  expect(resolveField(renamed, {})).toMatchObject({ name: "account", relation: { resource: "Account" } });
});

test("a FormSpec field's resolve may return only FormSpec members", () => {
  const listOnly: FormSpecFieldDescriptor = { name: "lines", resolve: () => ({ addLabel: "Add line", layout: "input" }) };
  expect(resolveField(listOnly, {})).toMatchObject({ name: "lines", addLabel: "Add line", layout: "input" });
});

test("resolveField and visibleFields return the caller's descriptor type", () => {
  const dialog: DescriptorField = { name: "account", relation: { resource: "Account" }, resolve: () => ({ readOnly: true }) };
  const spec: FormSpecFieldDescriptor = { name: "lines", addLabel: "Add line", resolve: () => ({ layout: "input" }) };
  const resolvedDialog: DescriptorField = resolveField(dialog, {});
  const resolvedSpec: FormSpecFieldDescriptor = resolveField(spec, {});
  expectTypeOf(resolveField(dialog, {})).toEqualTypeOf<DescriptorField>();
  expectTypeOf(resolveField(spec, {})).toEqualTypeOf<FormSpecFieldDescriptor>();
  expectTypeOf(visibleFields([dialog], {})).toEqualTypeOf<DescriptorField[]>();
  expect(resolvedDialog).toMatchObject({ name: "account", relation: { resource: "Account" }, readOnly: true });
  expect(resolvedSpec).toMatchObject({ name: "lines", addLabel: "Add line", layout: "input" });
});

test("explicit panes retain their long text and status fields instead of hoisting them into the hero", () => {
  const fields: readonly FieldDescriptor[] = [
    { name: "title", title: true },
    { name: "description", widget: "markdown.editor" },
    { name: "status", widget: "statusbar" },
  ];
  const layout = formViewFieldLayout(fields, fields, [{
    label: "Details", pane: "details", fields: fields.slice(1), actions: [],
  }], null);
  expect(layout.titleField?.name).toBe("title");
  expect(layout.bodyField).toBeUndefined();
  expect(layout.statusField).toBeUndefined();
  expect(layout.gridGroups[0]?.fields.map((field) => field.name)).toEqual(["description", "status"]);
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

test.each([undefined, true])("the statusbar widget owns header status and is excluded from fields and groups (status=%s)", (status) => {
  const state: FieldDescriptor = { name: "state", widget: "statusbar", status };
  const title: FieldDescriptor = { name: "number", title: true };
  const detail: FieldDescriptor = { name: "date" };
  const layout = formViewFieldLayout([title, state, detail], [title, state, detail], [
    { fields: [state, detail], actions: [] },
  ], null);
  expect(layout.statusField).toBe(state);
  expect(layout.gridFields).toEqual([detail]);
  expect(layout.gridGroups[0]?.fields).toEqual([detail]);
});

test("status selection preserves custom header widgets and ordinary body fields", () => {
  const stage: FieldDescriptor = { name: "stage", widget: "task.stage", status: true };
  const status: FieldDescriptor = { name: "status", widget: "select" };
  const custom = formViewFieldLayout([stage, status], [stage, status], [], null);
  expect(custom.statusField).toBe(stage);
  expect(custom.gridFields).toEqual([status]);
  expect(formViewFieldLayout([status], [status], [], null).statusField).toBeUndefined();
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

test("nested object fields qualify their owning model's row projection", () => {
  const paths = new Set<string>();
  addFieldSelection(paths, { name: "version.payload" }, undefined,
    { name: "payload", kind: "object" }, {
      kind: "object", scalar: null, values: [], nullable: true,
      row: { path: "payload", paths: ["payload.kind", "payload.target.id"] },
      filter: null, sort: null, relation: null,
    });
  expect([...paths]).toEqual(["version.payload.kind", "version.payload.target.id"]);
});

test("an empty date or time field drafts as null, never a blank wire string", () => {
  expect(emptyDraft([
    { name: "due", widget: "date" }, { name: "at", widget: "datetime" }, { name: "starts", widget: "time" },
  ])).toEqual({ due: null, at: null, starts: null });
});

test("an empty object or json field drafts as an empty object and an empty list or rows field as an empty list", () => {
  const structured: FormSpecFieldDescriptor[] = [
    { name: "layout", widget: "object", objectTemplate: [{ name: "heading" }] },
    { name: "steps", widget: "list", itemTemplate: { name: "step" } },
    { name: "mappings", widget: "rows", rowTemplate: [{ name: "source" }] },
    { name: "config", widget: "json" },
  ];
  const empty = { layout: {}, steps: [], mappings: [], config: {} };
  expect(emptyDraft(structured)).toStrictEqual(empty);
  expect(recordToValues({ id: "row-1" }, structured)).toStrictEqual(empty);
  expect(recordToValues({ id: "row-1", layout: null, steps: null, mappings: null, config: null }, structured))
    .toStrictEqual({ layout: null, steps: null, mappings: null, config: null });
});

test("a required json field holding an empty object is not missing, in a record form as in a dialog", () => {
  const config: FieldDescriptor = { name: "config", widget: "json", required: true };
  expect(formRuleErrors({ config: {} }, [config], new Set(), t)).toEqual({});
  expect(dialogRuleErrors([config], { config: {} }, t)).toEqual({});
});

test("a field's failed rule says the same thing in a record form as in a dialog", () => {
  const ruleFields: FieldDescriptor[] = [
    { name: "slug", required: true, minLength: 1, maxLength: 64 },
    { name: "cleared", minLength: 1 },
    { name: "nick", minLength: 1, omittable: true },
    { name: "caption", minLength: 1, nullable: true },
    { name: "handle", minLength: 3 },
    { name: "low", kind: "integer", minimum: 5 },
    { name: "high", kind: "number", maximum: 10 },
    { name: "count", kind: "integer", omittable: true },
    { name: "tags", kind: "array", minItems: 2 },
    { name: "limits", kind: "array", maxItems: 1 },
    { name: "token", presenceRequired: true },
    { name: "remark", presenceRequired: true },
    { name: "title", required: true },
    { name: "label", required: true, maxLength: 10 },
    { name: "auth", kind: "object", objectTemplate: [{ name: "secret", required: true }] },
  ];
  const values = {
    slug: "a".repeat(65), cleared: "", nick: "", caption: "", handle: "", low: 2, high: 11, count: "",
    tags: ["a"], limits: ["a", "b"], remark: null, title: " ", auth: {},
  };
  const expected = {
    slug: { type: "maxLength", message: "Enter no more than 64 characters." },
    cleared: required,
    nick: { type: "minLength", message: "Enter at least 1 character." },
    caption: { type: "minLength", message: "Enter at least 1 character." },
    handle: { type: "minLength", message: "Enter at least 3 characters." },
    low: { type: "minimum", message: "Enter a value of at least 5." },
    high: { type: "maximum", message: "Enter a value of at most 10." },
    count: { type: "type", message: "Enter a valid whole number." },
    tags: { type: "minItems", message: "Add at least 2 items." },
    limits: { type: "maxItems", message: "Keep no more than 1 item." },
    token: required,
    remark: required,
    title: required,
    label: required,
    auth: { secret: required },
  };
  expect(formRuleErrors(values, ruleFields, new Set(), t)).toEqual(expected);
  expect(dialogRuleErrors(ruleFields, values, t)).toEqual(expected);
});

test.each<[string, {
  field: FieldDescriptor; values: Record<string, unknown>;
  requiredFieldNames?: readonly string[]; validateConstraints?: boolean; recordForm: object; dialog: object;
}]>([
  [
    "a hidden required field: neither a record form nor a dialog checks it, since the person cannot see it",
    { field: { name: "token", required: true, hidden: true }, values: { token: "" }, recordForm: {}, dialog: {} },
  ],
  [
    "a read-only required field: neither a record form nor a dialog checks it, since the person cannot change it",
    { field: { name: "code", required: true, readOnly: true }, values: { code: "" }, recordForm: {}, dialog: {} },
  ],
  [
    "a structured field the model requires on create: a record form counts it as required, and a dialog does not",
    {
      field: { name: "secret", maxLength: 64 }, values: {}, requiredFieldNames: ["secret"],
      recordForm: { secret: required }, dialog: {},
    },
  ],
  [
    "guard: a value breaking a bound: a record form refuses it, and a dialog with a schema resolver leaves it to that resolver",
    {
      field: { name: "code", maxLength: 2 }, values: { code: "abc" }, validateConstraints: false,
      recordForm: { code: { type: "maxLength", message: "Enter no more than 2 characters." } }, dialog: {},
    },
  ],
])("%s", (_label, { field, values, requiredFieldNames = [], validateConstraints, recordForm, dialog }) => {
  expect(formRuleErrors(values, [field], new Set(requiredFieldNames), t)).toStrictEqual(recordForm);
  expect(dialogRuleErrors([field], values, t, validateConstraints)).toStrictEqual(dialog);
});

test("a dialog reads an own key holding undefined as absent, as a record form does: a bounded optional field holding one fails no rule", () => {
  expect(dialogRuleErrors([{ name: "nickname", maxLength: 64 }], { nickname: undefined }, t)).toStrictEqual({});
  expect(formRuleErrors({ nickname: undefined }, [{ name: "nickname", maxLength: 64 }], new Set(), t)).toStrictEqual({});
});

test("guard: a record form leaves out a field read-only as resolved for its values: by its own flag, its resolve, or its mode", () => {
  const fields = withModeLockedFields([
    { name: "title", required: true },
    { name: "code", required: true, readOnly: true },
    { name: "slug", required: true, resolve: () => ({ readOnly: true }) },
    { name: "kind", required: true, createOnly: true },
  ], false);
  expect(formRuleErrors({ title: "", code: "", slug: "", kind: "" }, fields, new Set(), t)).toStrictEqual({ title: required });
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
        required: values.backend_class === "strict",
      }),
    };
    expect(formRuleErrors(
      { backend_class: "strict", config: { shared: "" } }, [dynamic], new Set(), t,
    )).toEqual({ config: { shared: required } });
    expect(formRuleErrors(
      { backend_class: "loose", config: { shared: "" } }, [dynamic], new Set(), t,
    )).toEqual({});
  });

  test("accepts explicit null only for a nullable required descriptor", () => {
    expect(formRuleErrors(
      { note: null }, [{ name: "note", required: true, nullable: true, presenceRequired: true }], new Set(), t,
    )).toEqual({});
    expect(formRuleErrors(
      { note: null }, [{ name: "note", required: true }], new Set(), t,
    )).toEqual({ note: required });
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
    expect(formRuleErrors({ title: "", items: [] }, presenceFields, new Set(), t)).toEqual({});
    expect(formRuleErrors({ title: "" }, presenceFields, new Set(), t)).toEqual({ items: required });
    expect(formRuleErrors(
      { title: "", items: [] }, [{ ...presenceFields[0]!, minLength: 1 }, { ...presenceFields[1]!, minItems: 1 }], new Set(), t,
    )).toEqual({
      title: required,
      items: { type: "minItems", message: "Add at least 1 item." },
    });
    expect(formRuleErrors(
      { title: "" }, [{ name: "title", required: true }], new Set(), t,
    )).toEqual({ title: required });
    const optional = { name: "summary", omittable: true, minLength: 2 };
    expect(formRuleErrors({}, [optional], new Set(), t)).toEqual({});
    expect(formRuleErrors({ summary: "" }, [optional], new Set(), t)).toEqual({
      summary: { type: "minLength", message: "Enter at least 2 characters." },
    });
    // A field that is presence-required without `required` holds the same rule.
    const presenceOnly = presenceFields.map(({ required: _required, ...field }) => field);
    expect(formRuleErrors({ title: " ", items: [] }, presenceOnly, new Set(), t)).toEqual({});
    expect(formRuleErrors({ title: "" }, presenceOnly, new Set(), t)).toEqual({ items: required });
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

test("a to-many relation keeps an equal re-picked set as its baseline and submits public ids", () => {
  const baseline = [{ id: "user-1", display_name: "Ada" }, { id: "user-2", display_name: "Grace" }];
  expect(relationListValue(["user-2", "user-1"], baseline)).toEqual(baseline);
  expect(relationListValue(["user-2", "user-3"], baseline)).toEqual([baseline[1], "user-3"]);
  expect(relationListValue([], undefined)).toEqual([]);
  expect(mutationData(
    { members: [baseline[0], "user-3"] },
    [{ name: "members", widget: "many2many" }],
    { dirtyFields: { members: true }, id: "team-1", isCreate: false },
  )).toEqual({ id: "team-1", members: ["user-1", "user-3"] });
  expect(emptyDraft([{ name: "members", widget: "many2many" }])).toEqual({ members: [] });
});

describe("formSections", () => {
  test("a lines group is the lines section, kept without fields, in its declared order", () => {
    const sections = formSections([], [
      { label: "Identity", fields: [{ name: "key" }], actions: [] },
      { label: "Stages", lines: true, fields: [], actions: [] },
      { label: "Empty", fields: [], actions: [] },
    ], [10, 50, 60]);
    expect(sections.map((section) => [section.key, section.label, section.sequence])).toEqual([
      ["group:0:Identity", "Identity", 10],
      [EDITABLE_LINES_SECTION, "Stages", 50],
    ]);
  });

  test("a form declares its lines in one group", () => {
    expect(() => formSections([], [
      { label: "One", lines: true, fields: [], actions: [] },
      { label: "Two", lines: true, fields: [], actions: [] },
    ])).toThrow("one group");
  });
});

test("a saved-only group's fields stay off a create form instead of falling back to the ungrouped section", () => {
  const name = { name: "name" };
  const archived = { name: "is_archived" };
  const groups = [{ fields: [archived], actions: [], savedOnly: true }];
  expect(formSections([name, archived], groups, [], true)).toEqual([{ key: "fields", fields: [name] }]);
  expect(formSections([name, archived], groups, [], false).map((section) => section.fields)).toEqual([[name], [archived]]);
});

describe("seedPreset and PresetMemory", () => {
  /** A form as a preset sees it: values and dirty paths, with every read and write recorded. */
  function form(initial: Record<string, unknown> = {}, dirty: readonly string[] = []) {
    const values = structuredClone(initial);
    const dirtyPaths = new Set(dirty);
    const reads: [string, unknown][] = [];
    const writes: [string, unknown, boolean][] = [];
    return {
      values, reads, writes,
      read: (path: string) => {
        reads.push([path, structuredClone(get(values, path))]);
        return { dirty: dirtyPaths.has(path), value: get(values, path) };
      },
      write: (path: string, seed: unknown, dirties: boolean) => {
        writes.push([path, seed, dirties]);
        set(values, path, seed);
        if (dirties) dirtyPaths.add(path);
      },
    };
  }
  const seed = (
    preset: FieldDescriptor,
    target: ReturnType<typeof form>,
    options: { isCreate?: boolean; memory?: PresetMemory; at?: (path: string) => string; loaded?: Record<string, unknown> } = {},
  ) => seedPreset(preset, "chosen", target.values, options.loaded, {
    isCreate: options.isCreate ?? false,
    memory: options.memory ?? createPresetMemory(),
    ...(options.at ? { at: options.at } : {}),
    read: target.read,
    write: target.write,
  });

  test("every seed dirties the form, in the preset's order, and the written paths come back", () => {
    const target = form({ note: "now" });
    const preset: FieldDescriptor = {
      name: "kind",
      prefill: (value, values, loaded) => ({ zeta: value, alpha: values.note, mid: loaded?.note, empty: undefined }),
    };
    expect(seed(preset, target, { loaded: { note: "then" } })).toEqual(["zeta", "alpha", "mid", "empty"]);
    expect(target.writes).toEqual([["zeta", "chosen", true], ["alpha", "now", true], ["mid", "then", true], ["empty", undefined, true]]);
    expect(target.reads).toEqual([]);
  });

  test("a createOnly preset writes only while the form creates", () => {
    const preset: FieldDescriptor = { name: "kind", createOnly: true, prefill: () => ({ config: {} }) };
    const editing = form();
    expect(seed(preset, editing)).toEqual([]);
    expect(editing.writes).toEqual([]);
    const creating = form();
    expect(seed(preset, creating, { isCreate: true })).toEqual(["config"]);
    expect(creating.writes).toEqual([["config", {}, true]]);
  });

  test("a preset of nothing writes nothing", () => {
    for (const preset of [{ name: "kind" }, { name: "kind", prefill: () => null }, { name: "kind", prefill: () => undefined }]) {
      const target = form();
      expect(seed(preset, target)).toEqual([]);
      expect(target.writes).toEqual([]);
    }
  });

  test("a dirty-preserving preset skips the person's own values, writes the rest undirtied, and replaces what it names", () => {
    const memory = createPresetMemory();
    memory.noteEdit("scope.secret");
    const target = form({ scope: { secret: "", config: "typed config", style: "" } }, ["scope.config"]);
    const preset: FieldDescriptor = {
      name: "kind",
      prefill: () => ({ secret: "preset", style: "plain", config: { mode: "fresh" }, note: "" }),
      prefillPreserveDirty: true,
      prefillReplace: ["config"],
    };
    expect(seed(preset, target, { memory, at: (path) => `scope.${path}` })).toEqual(["scope.style", "scope.config", "scope.note"]);
    expect(target.writes).toEqual([
      ["scope.style", "plain", false], ["scope.config", { mode: "fresh" }, true], ["scope.note", "", false],
    ]);
    // An undirtied seed is remembered as the preset's; a dirtying one is not.
    expect(memory.isOwn("scope.style", { dirty: true, value: "plain" })).toBe(false);
    expect(memory.isOwn("scope.config", { dirty: true, value: { mode: "fresh" } })).toBe(true);
  });

  test("a value is the person's own when they changed it, or when it is dirty and no longer the seed written there", () => {
    const memory = createPresetMemory();
    memory.noteEdit("edited");
    expect(memory.isOwn("edited", { dirty: false, value: "" })).toBe(true);
    expect(memory.isOwn("untouched", { dirty: false, value: "x" })).toBe(false);
    expect(memory.isOwn("untouched", { dirty: true, value: "x" })).toBe(true);
    memory.noteSeed("seeded", { mode: "fresh" });
    expect(memory.isOwn("seeded", { dirty: true, value: { mode: "fresh" } })).toBe(false);
    expect(memory.isOwn("seeded", { dirty: true, value: { mode: "mine" } })).toBe(true);
  });

  test("a cleared memory forgets every edit and seed", () => {
    const memory = createPresetMemory();
    memory.noteEdit("edited");
    memory.noteSeed("seeded", "plain");
    memory.clear();
    expect(memory.isOwn("edited", { dirty: false, value: "" })).toBe(false);
    expect(memory.isOwn("seeded", { dirty: true, value: "plain" })).toBe(true);
  });

  test("a target is read on its turn, after the seeds before it were written", () => {
    const target = form({ settings: { a: "typed" } });
    const preset: FieldDescriptor = {
      name: "kind",
      prefill: () => ({ settings: { a: "seeded", b: 1 }, "settings.a": "again" }),
      prefillPreserveDirty: true,
      prefillReplace: ["settings"],
    };
    expect(seed(preset, target)).toEqual(["settings", "settings.a"]);
    expect(target.reads).toEqual([["settings.a", "seeded"]]);
    expect(target.values).toEqual({ settings: { a: "again", b: 1 } });
  });
});
