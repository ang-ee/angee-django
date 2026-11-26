// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets, type WidgetDefinition, type WidgetRenderProps } from "../../widgets";
import type { ActionArg, FieldDescriptor } from "../page";
import { ActionFormDialog } from "./ActionFormDialog";
import { emptyValueForField, fieldRuleErrors, isFormSpecValueCompatible, refusesSeed } from "./field-values";
import { deserializeFormSpec, formSpecInitialValues, type FormSpecFieldDescriptor } from "./form-spec";
import { emptyDraft } from "./form-view-model";
import { MutationDialog } from "./MutationDialog";

describe("a field's empty value (emptyValueForField), one row of its table per line", () => {
  test.each<[string, Pick<FieldDescriptor, "widget" | "kind" | "format">, unknown]>([
    ["1. a declared format: a date carried as text", { kind: "string", format: "date" }, null],
    ["1. a declared format: a date and time carried as text", { kind: "string", format: "date-time", widget: "datetime" }, null],
    ["1. a declared format: a time carried as text", { kind: "string", format: "time", widget: "text" }, null],
    ["1. a declared format with no kind", { format: "date" }, null],
    ["1. an explicit string kind", { kind: "string" }, ""],
    ["1. a widget does not override an explicit string kind: date", { kind: "string", widget: "date" }, ""],
    ["1. a widget does not override an explicit string kind: date and time", { kind: "string", widget: "datetime" }, ""],
    ["1. a widget does not override an explicit string kind: time", { kind: "string", widget: "time" }, ""],
    ["1. a widget does not override an explicit string kind: float", { kind: "string", widget: "float" }, ""],
    ["1. a widget does not override an explicit string kind: integer", { kind: "string", widget: "integer" }, ""],
    ["1. a widget does not override an explicit string kind: json", { kind: "string", widget: "json" }, ""],
    ["2. the integer widget", { widget: "integer" }, null],
    ["2. the float widget", { widget: "float" }, null],
    ["2. the date widget", { widget: "date" }, null],
    ["2. the datetime widget", { widget: "datetime" }, null],
    ["2. the time widget", { widget: "time" }, null],
    ["2. a datetime kind resolves the datetime widget", { kind: "datetime" }, null],
    ["3. the tagInput widget", { widget: "tagInput" }, []],
    ["3. a relation list", { widget: "many2many" }, []],
    ["3. the list widget", { widget: "list" }, []],
    ["3. the rows widget", { widget: "rows" }, []],
    ["4. the switch widget", { widget: "switch" }, false],
    ["4. the object widget", { widget: "object" }, {}],
    ["4. the json widget of an array kind", { kind: "array", widget: "json" }, []],
    ["4. the json widget of an any kind, a form spec's untyped value", { kind: "any", widget: "json" }, null],
    ["4. the json widget of an object kind", { kind: "object", widget: "json" }, {}],
    ["4. the json widget with no kind, as the metadata gives it", { widget: "json" }, {}],
    ["5. an array kind", { kind: "array" }, []],
    ["5. an object kind", { kind: "object" }, {}],
    ["5. a boolean kind", { kind: "boolean" }, false],
    ["5. a boolean kind under the boolean widget", { kind: "boolean", widget: "boolean" }, false],
    ["5. a switch kind", { kind: "switch" }, false],
    ["5. an integer kind", { kind: "integer" }, null],
    ["5. a number kind", { kind: "number" }, null],
    ["5. an any kind", { kind: "any" }, null],
    ["5. an integer kind under the select widget", { kind: "integer", widget: "select" }, ""],
    ["5. an any kind under the select widget", { kind: "any", widget: "select" }, ""],
    ["6. no widget and no kind", {}, ""],
    ["6. the text widget", { widget: "text" }, ""],
    ["6. the select widget", { widget: "select" }, ""],
    ["6. the boolean widget with no kind", { widget: "boolean" }, ""],
    ["6. a known hole kept on purpose until the descriptor carries its declared type: the booleanToggle widget with no kind", { widget: "booleanToggle" }, ""],
    ["6. a known hole kept on purpose until the descriptor carries its declared type: the booleanBadge widget with no kind", { widget: "booleanBadge" }, ""],
    ["6. a known hole kept on purpose until the descriptor carries its declared type: the percent widget with no kind", { widget: "percent" }, ""],
    ["6. a known hole kept on purpose until the descriptor carries its declared type: the progressBar widget with no kind", { widget: "progressBar" }, ""],
    ["6. a known hole kept on purpose until the descriptor carries its declared type: the money widget with no kind", { widget: "money" }, ""],
  ])("%s", (_row, descriptor, expected) => {
    expect(emptyValueForField({ name: "field", ...descriptor })).toStrictEqual(expected);
  });
});

describe("the values a field's declared FormSpec type admits (isFormSpecValueCompatible)", () => {
  test.each<[string, Pick<FieldDescriptor, "kind" | "nullable">, unknown, boolean]>([
    ["a text admits a text", { kind: "string" }, "", true],
    ["a text does not admit a number", { kind: "string" }, 1, false],
    ["an integer admits a whole number", { kind: "integer" }, 2, true],
    ["an integer does not admit a fraction", { kind: "integer" }, 1.5, false],
    ["a number admits a fraction", { kind: "number" }, 1.5, true],
    ["a number does not admit an infinite value", { kind: "number" }, Number.POSITIVE_INFINITY, false],
    ["a number does not admit NaN", { kind: "number" }, Number.NaN, false],
    ["a boolean admits false", { kind: "boolean" }, false, true],
    ["an object admits an object", { kind: "object" }, {}, true],
    ["an object does not admit a list", { kind: "object" }, [], false],
    ["a list admits a list", { kind: "array" }, [], true],
    ["a list does not admit an object", { kind: "array" }, {}, false],
    ["an untyped value admits a list", { kind: "any" }, [], true],
    ["null only where the field is nullable", { kind: "integer", nullable: true }, null, true],
    ["null not where it is not", { kind: "integer" }, null, false],
    ["a field without a FormSpec kind admits a null it allows", { nullable: true }, null, true],
    ["a field without a FormSpec kind admits no other value", {}, "", false],
  ])("%s", (_case, field, value, admitted) => {
    expect(isFormSpecValueCompatible(field, value)).toBe(admitted);
  });
});

describe("a member that may be left out stays absent rather than take a seed its own type refuses (refusesSeed)", () => {
  test.each<[string, Pick<FieldDescriptor, "kind" | "nullable" | "required">, unknown, boolean]>([
    ["an integer refuses null", { kind: "integer" }, null, true],
    ["a number refuses a blank text", { kind: "number" }, "", true],
    ["a text refuses null", { kind: "string" }, null, true],
    ["a nullable integer takes null", { kind: "integer", nullable: true }, null, false],
    ["a required integer takes null: a required member is seeded", { kind: "integer", required: true }, null, false],
    ["a text takes a blank text", { kind: "string" }, "", false],
    ["a boolean takes false", { kind: "boolean" }, false, false],
    ["a list takes an empty list", { kind: "array" }, [], false],
    ["an object takes an empty object", { kind: "object" }, {}, false],
    ["an untyped value takes null", { kind: "any" }, null, false],
    ["a field with no kind takes null", {}, null, false],
    ["a hand-written kind that is not a FormSpec type takes null", { kind: "datetime" }, null, false],
  ])("%s", (_case, field, seed, refused) => {
    expect(refusesSeed(field, seed)).toBe(refused);
  });
});

// The shapes a form spec emits (as measured for this rule): each base shape, and six of them
// required, nullable, omittable and presence-required.
const SHAPES: Readonly<Record<string, Readonly<Record<string, unknown>>>> = {
  string: { type: "string" },
  stringFormatDate: { type: "string", format: "date" },
  stringFormatDateTime: { type: "string", format: "date-time" },
  stringWidgetDate: { type: "string", widget: "date" },
  stringWidgetDatetime: { type: "string", widget: "datetime" },
  stringWidgetTime: { type: "string", widget: "time" },
  stringWidgetTextarea: { type: "string", widget: "textarea" },
  stringWidgetSlug: { type: "string", widget: "slug" },
  stringWidgetFloat: { type: "string", widget: "float" },
  stringWidgetInteger: { type: "string", widget: "integer" },
  stringWidgetMarkdown: { type: "string", widget: "markdown.editor" },
  stringEnum: { type: "string", enum: ["a", "b"] },
  stringOptions: { type: "string", options: [{ value: "a", label: "A" }] },
  stringRelation: { type: "string", relation: { resource: "parties.Party" } },
  stringNullableAnyOf: { anyOf: [{ type: "string" }, { type: "null" }] },
  stringNullableTypeList: { type: ["string", "null"] },
  stringNullableFlag: { type: "string", nullable: true },
  stringDefault: { type: "string", default: "x" },
  stringDefaultValue: { type: "string", defaultValue: "x" },
  stringOmittable: { type: "string", omittable: true },
  stringPresenceRequired: { type: "string", presenceRequired: true },
  integer: { type: "integer" },
  integerNullable: { anyOf: [{ type: "integer" }, { type: "null" }] },
  integerEnum: { type: "integer", enum: [1, 2] },
  integerWidgetSelect: { type: "integer", widget: "select", options: [{ value: 1, label: "One" }] },
  number: { type: "number" },
  decimalUnion: { anyOf: [{ type: "number" }, { type: "string", pattern: "^-?\\d+(\\.\\d+)?$" }] },
  decimalUnionNullable: { anyOf: [{ type: "number" }, { type: "string", pattern: "^-?\\d+(\\.\\d+)?$" }, { type: "null" }] },
  boolean: { type: "boolean" },
  booleanWidgetSwitch: { type: "boolean", widget: "switch" },
  booleanNullable: { anyOf: [{ type: "boolean" }, { type: "null" }] },
  booleanConstDefault: { type: "boolean", const: true, default: false },
  booleanOmittable: { type: "boolean", omittable: true },
  objectBare: { type: "object" },
  objectProperties: { type: "object", properties: { x: { type: "string" } } },
  objectWidgetObject: { type: "object", widget: "object", properties: { x: { type: "string" } } },
  objectWidgetJson: { type: "object", widget: "json" },
  objectWidgetJsonProperties: { type: "object", widget: "json", properties: { x: { type: "string" } } },
  objectNullableProperties: { anyOf: [{ type: "object", properties: { x: { type: "string" } } }, { type: "null" }] },
  objectOmittableJson: { type: "object", widget: "json", omittable: true },
  arrayBare: { type: "array" },
  arrayItemsString: { type: "array", items: { type: "string" } },
  arrayWidgetList: { type: "array", widget: "list", items: { type: "string" } },
  arrayWidgetListObjects: { type: "array", widget: "list", items: { type: "object", properties: { x: { type: "string" } } } },
  arrayItemsObject: { type: "array", items: { type: "object", properties: { x: { type: "string" } } } },
  arrayWidgetTagInput: { type: "array", widget: "tagInput", items: { type: "string" } },
  arrayWidgetJson: { type: "array", widget: "json" },
  arrayNullable: { anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }] },
  untyped: {},
  untypedWidgetJson: { widget: "json" },
  typeNull: { type: "null" },
  typeListStringInteger: { type: ["string", "integer"] },
  anyOfStringInteger: { anyOf: [{ type: "string" }, { type: "integer" }] },
  anyWidgetJsonOmittable: { type: "any", widget: "json", omittable: true },
};
const FLAGGED = Object.fromEntries(["string", "integer", "boolean", "objectBare", "arrayItemsString", "untyped"].flatMap((name) => [
  [`${name}__required`, SHAPES[name]],
  [`${name}__nullable`, { ...SHAPES[name], nullable: true }],
  [`${name}__omittable`, { ...SHAPES[name], omittable: true }],
  [`${name}__presenceRequired`, { ...SHAPES[name], presenceRequired: true }],
]));
const ALL_SHAPES = { ...SHAPES, ...FLAGGED };
// Every member required, so that no surface leaves one out for a seed its type refuses.
const EMITTED = deserializeFormSpec(
  { type: "object", properties: ALL_SHAPES, required: Object.keys(ALL_SHAPES) },
  defaultWidgets,
);
// Where a seeding policy decides instead of the empty value (a default, an omittable or
// presence-required member, `null` for a nullable one, an object's template), the three seeding
// policies keep their recorded differences; every other shape is seeded with its empty value.
const ONE_EMPTY_VALUE = EMITTED.filter((field) => !field.hasDefault && !field.omittable && !field.presenceRequired
  && !field.nullable && !field.objectTemplate);
// An action argument carries no template and no relation, and its submit writes a date and time as the
// stored moment, so these shapes are not action arguments.
const ARGUMENT = (field: FormSpecFieldDescriptor) => !field.itemTemplate && !field.rowTemplate && !field.relation
  && field.widget !== "datetime";

const { Provider, clearClients } = createUiTestProviders({});
afterEach(() => { cleanup(); clearClients(); });
const own = (values: Readonly<Record<string, unknown>>, name: string) => Object.hasOwn(values, name) ? values[name] : "(absent)";

// The seeds are the surfaces', not the widgets': an editor that mounts a code editor is a plain stand-in
// here, since a code editor still updating when a test unmounts it throws outside the test.
const plainEditor: WidgetDefinition = { read: ({ value }: WidgetRenderProps) => <span>{JSON.stringify(value) ?? ""}</span> };
const seedWidgets = { ...defaultWidgets, json: plainEditor, "markdown.editor": plainEditor };

function Shell({ children }: { children: React.ReactNode }) {
  const router = React.useMemo(() => createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) }), []);
  return <RouterContextProvider router={router}><ModalsHost><ToastProvider><Provider>
    <AppRuntimeProvider runtime={{ widgets: seedWidgets }}>{children}</AppRuntimeProvider>
  </Provider></ToastProvider></ModalsHost></RouterContextProvider>;
}

/** The seed a MutationDialog gives `field`, read from what its readiness check receives (a read-only field never blocks it). */
async function mutationDialogSeed(field: FormSpecFieldDescriptor): Promise<unknown> {
  const seen = vi.fn((_values: Readonly<Record<string, unknown>>) => true);
  render(<Shell><MutationDialog open onOpenChange={() => undefined} title="Seed" fields={[{ ...field, readOnly: true }]}
    submitLabel="Go" parseValues={(values) => values} canSubmit={seen} onSubmit={async () => ({ status: "ok", data: undefined })} /></Shell>);
  await waitFor(() => expect(seen).toHaveBeenCalled());
  return own(seen.mock.calls.at(-1)![0], field.name);
}

/** What an action dialog's submit receives for `field` as an optional argument left untouched. */
async function actionArgumentSeed(field: FormSpecFieldDescriptor): Promise<unknown> {
  const submit = vi.fn(async (_values: Record<string, unknown>) => ({ ok: true, message: "" }));
  // Typed, not cast: an argument that could not carry the field's format would not compile.
  const argument: ActionArg = { name: field.name, label: "Argument", widget: field.widget, kind: field.kind, format: field.format, options: field.options, optional: true };
  render(<Shell><ActionFormDialog action={{ id: "seed", label: "Run", args: [argument], submit }}
    context={{ record: null, selectedIds: [] }} open onOpenChange={() => undefined} /></Shell>);
  const button = await screen.findByRole<HTMLButtonElement>("button", { name: "Run" });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  return own(submit.mock.calls[0]![0], field.name);
}

describe("the record form, a mutation dialog and an action argument seed a hand-written descriptor with a widget and no kind with one empty value", () => {
  // A hand-written descriptor carries the widget the metadata or its author names; the list, rows,
  // object and relation widgets render only with a template or a relation, so the draft answers alone.
  test.each(["date", "datetime", "time", "integer", "float", "tagInput", "switch", "json", "select", "text"])("%s", async (widget) => {
    const field = { name: "value", label: "Value", widget } as FormSpecFieldDescriptor;
    const empty = emptyValueForField(field);
    expect(own(emptyDraft([field]), field.name)).toStrictEqual(empty);
    expect(await mutationDialogSeed(field)).toStrictEqual(empty);
    cleanup();
    if (ARGUMENT(field)) expect(await actionArgumentSeed(field)).toStrictEqual(empty);
  });

  test.each(["list", "rows", "object", "many2many"])("%s: the record form's draft", (widget) => {
    const field = { name: "value", widget };
    expect(own(emptyDraft([field]), field.name)).toStrictEqual(emptyValueForField(field));
  });
});

describe("the record form and the three dialogs seed each emitted shape with one empty value", () => {
  test.each(ONE_EMPTY_VALUE.map((field) => [field.name, field] as const))("%s", async (_name, field) => {
    const empty = emptyValueForField(field);
    expect(own(emptyDraft([field]), field.name)).toStrictEqual(empty);
    expect(own(formSpecInitialValues([field], {}), field.name)).toStrictEqual(empty);
    expect(await mutationDialogSeed(field)).toStrictEqual(empty);
    cleanup();
    if (ARGUMENT(field)) expect(await actionArgumentSeed(field)).toStrictEqual(empty);
  });
});

describe("the rule check and the seed rule agree", () => {
  // Every way a field can be declared that the seed and the check read: its widget, its kind, one
  // bound, and whether it may be null or left out.
  const WIDGETS = [undefined, "integer", "float", "percent", "progressBar", "money", "date", "datetime", "time", "text", "select", "switch", "json", "tagInput", "list"] as const;
  const KINDS = [undefined, "string", "integer", "number", "boolean", "array", "object", "any", "datetime"] as const;
  const BOUNDS: readonly Partial<FieldDescriptor>[] = [
    {}, { minimum: 0 }, { maximum: 10 }, { minLength: 1 }, { maxLength: 5 }, { minItems: 1 }, { maxItems: 3 },
  ];
  const FLAGS: readonly Partial<FieldDescriptor>[] = [{}, { nullable: true }, { omittable: true }, { nullable: true, omittable: true }];
  const shapes = WIDGETS.flatMap((widget) => KINDS.flatMap((kind) => BOUNDS.flatMap((bound) => FLAGS.map((flags) => ({
    name: "value", ...(widget ? { widget } : {}), ...(kind ? { kind } : {}), ...bound, ...flags,
  } as FieldDescriptor)))));
  const label = ({ name: _name, ...shape }: FieldDescriptor) => JSON.stringify(shape);
  /** Whether the seeding paths give this field `null` when it holds nothing. */
  const seededNull = (shape: FieldDescriptor) => emptyValueForField(shape) === null && !refusesSeed(shape, null);
  /** What the check reads at every depth: a presence flag, a template or a bound. */
  const structured = (shape: FieldDescriptor) => Boolean(shape.presenceRequired || shape.omittable
    || ["minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"].some((bound) => shape[bound as keyof FieldDescriptor] !== undefined));
  const failing = (cases: readonly FieldDescriptor[], verdict: (shape: FieldDescriptor) => readonly string[], expected: (shape: FieldDescriptor) => readonly string[]) =>
    cases.filter((shape) => JSON.stringify(verdict(shape)) !== JSON.stringify(expected(shape)))
      .map((shape) => `${label(shape)}: ${verdict(shape).join(", ") || "accepted"}, expected ${expected(shape).join(", ") || "accepted"}`);

  // Presence is asked the same way whether the surface checks the constraints itself or leaves
  // them to a schema resolver (the schema dialog and a resolver-backed action dialog).
  describe.each([["checked", true], ["left to a schema resolver", false]] as const)("with the constraints %s", (_how, validateConstraints) => {
    /** The rules a field fails at its own path when it holds `value`. */
    const check = (field: FieldDescriptor, value: unknown, required = false) =>
      fieldRuleErrors(field, { value, present: true }, { required, validateConstraints }).map((error) => error.keyword);
    const inObject = (member: FieldDescriptor, value: unknown) => fieldRuleErrors(
      { name: "config", kind: "object", objectTemplate: [member] },
      { value: { [member.name]: value }, present: true }, { required: false, validateConstraints },
    ).map((error) => `${error.path}: ${error.keyword}`);
    const inList = (item: FieldDescriptor, value: unknown) => fieldRuleErrors(
      { name: "limits", kind: "array", itemTemplate: item },
      { value: [value], present: true }, { required: false, validateConstraints },
    ).map((error) => `${error.path}: ${error.keyword}`);

    test("an optional field that holds the `null` it is seeded with when it holds nothing is never refused, whatever its widget, kind, bound and flags", () => {
      const seeded = shapes.filter(seededNull);
      // The space holds the shape the defect was found in, and many besides.
      expect(seeded.map(label)).toContain(JSON.stringify({ widget: "integer", minimum: 0 }));
      expect(seeded.length).toBeGreaterThan(200);
      expect(failing(seeded, (shape) => check(shape, null), () => [])).toEqual([]);
    });

    test("only `null` is a seed: a key that holds `undefined` is still missing", () => {
      const seeded = shapes.filter(seededNull).filter(structured);
      expect(seeded.length).toBeGreaterThan(150);
      expect(failing(seeded, (shape) => check(shape, undefined), () => ["required"])).toEqual([]);
      expect(seeded.filter((member) => !inObject(member, undefined).includes("config.value: required")).map(label)).toEqual([]);
    });

    test("a required field, and one whose presence is required, answers for a `null` as it always did: refused unless it may be null", () => {
      // What `required` asks of a `null`, whatever seeded it: a structured field that may be null holds it
      // as a value; any other field is missing.
      const asked = (shape: FieldDescriptor) => structured(shape) && shape.nullable ? [] : ["required"];
      expect(failing(shapes, (shape) => check(shape, null, true), asked)).toEqual([]);
      const presenceRequired = shapes.map((shape) => ({ ...shape, presenceRequired: true }));
      expect(failing(presenceRequired, (shape) => check(shape, null), (shape) => shape.nullable ? [] : ["required"])).toEqual([]);
      // The cases a form builds itself: a required, nullable, bounded number seeded with `null`.
      expect(check({ name: "limit", kind: "integer", nullable: true, minimum: 0 }, null, true)).toEqual([]);
      expect(check({ name: "limit", widget: "integer", nullable: true, minimum: 0 }, null, true)).toEqual([]);
    });

    test("a member of an object field follows the same rule at its own path", () => {
      const members = shapes.filter(structured);
      const refused = (verdict: readonly string[]) => verdict.includes("config.value: required");
      // Optional and holding its own seed: not refused.
      expect(members.filter(seededNull).filter((member) => refused(inObject(member, null))).map(label)).toEqual([]);
      expect(members.filter(seededNull).length).toBeGreaterThan(150);
      // Required: refused unless it may be null, as before.
      expect(members.filter((member) => refused(inObject({ ...member, required: true }, null)) === Boolean(member.nullable)).map(label)).toEqual([]);
      // A typed member whose kind refuses `null` is never seeded with it; holding one all the same is refused.
      expect(inObject({ name: "value", kind: "integer", minimum: 0 }, null)).toEqual(["config.value: required"]);
    });

    test("a list item that holds nothing is missing, whatever seeded it: an item has no key to leave out", () => {
      const items = shapes.filter(structured);
      // Not nullable: an item holding `null` is refused, the shapes a form seeds with `null` included.
      expect(items.filter(seededNull).filter((item) => !item.nullable).length).toBeGreaterThan(80);
      expect(items.filter((item) => !item.nullable).filter((item) => !inList(item, null).includes("limits.0: required")).map(label)).toEqual([]);
      // Nullable: `null` is a value an item may hold.
      expect(items.filter((item) => item.nullable).filter((item) => inList(item, null).includes("limits.0: required")).map(label)).toEqual([]);
      expect(inList({ name: "value", widget: "integer", minimum: 0 }, 3)).toEqual([]);
    });
  });

  test("guards: a value outside its bound is refused, and so is a `null` that is not the field's own empty value", () => {
    const check = (field: FieldDescriptor, value: unknown) =>
      fieldRuleErrors(field, { value, present: true }, { required: false, validateConstraints: true }).map((error) => error.keyword);
    const limit: FieldDescriptor = { name: "limit", widget: "integer", minimum: 0 };
    expect(check(limit, null)).toEqual([]);
    expect(check(limit, -1)).toEqual(["minimum"]);
    expect(check(limit, 3)).toEqual([]);
    // A typed field whose own kind refuses `null` is not seeded with it.
    const typed: FieldDescriptor = { name: "limit", widget: "integer", kind: "integer", minimum: 0 };
    expect(refusesSeed(typed, null)).toBe(true);
    expect(check(typed, null)).toEqual(["required"]);
    // No kind, so the seed rule refuses nothing; its empty value is a text, so a `null` is not its seed.
    const text: FieldDescriptor = { name: "code", widget: "text", minLength: 1 };
    expect(emptyValueForField(text)).toBe("");
    expect(refusesSeed(text, null)).toBe(false);
    expect(check(text, null)).toEqual(["required"]);
  });
});
