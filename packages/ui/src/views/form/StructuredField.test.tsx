// @vitest-environment happy-dom

import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { deserializeFormSpec, formSpecInitialValues, normalizeFormSpecValues } from "./form-spec";
import { LabeledDescriptorField } from "./DescriptorFieldList";
import { listWidget, objectWidget } from "./StructuredField";
import { fieldRuleErrors, isEmptyFieldValue, structuredFieldErrors } from "./field-values";
import type { FieldDescriptor } from "../page";
import type { WidgetRenderProps } from "../../widgets/types";
import { RowsField, type RowsValue } from "./RowsField";

const metadata = schemaFieldMetadataFromDataResources([]);

describe("structured FormSpec widgets", () => {
  test("edits titled fixed rows and nested lists without losing hidden retained values", async () => {
    const [field] = sectionedFields();
    const changes = vi.fn();
    const focusRef = vi.fn<(target: { focus(): void } | null) => void>();
    const retainedRow = { identity: "row-1", title: "First", lines: [
      { identity: "line-1", fingerprint: "fingerprint-1", description: "Original" },
    ] };
    const secondRow = { identity: "row-2", title: "Second", lines: [] };
    function Harness() {
      const [value, setValue] = React.useState<RowsValue>([retainedRow, secondRow]);
      return <ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{ widgets: sectionedWidgets }}>
        <LabeledDescriptorField
          field={field!}
          value={value}
          messages={["Review the records.", "records.0.lines.0.description: Correct this description."]}
          controlRef={focusRef}
          onChange={(next) => { changes(next); setValue(next as RowsValue); }}
        />
      </AppRuntimeProvider></ModelMetadataProvider>;
    }
    render(<Harness />);

    expect(await screen.findByRole("heading", { name: "First" })).toBeTruthy();
    expect(screen.queryByText("Identity")).toBeNull();
    expect(screen.queryByText("Fingerprint")).toBeNull();
    const title = screen.getAllByRole("textbox", { name: "Title" })[0]!;
    const description = await screen.findByRole("textbox", { name: "Description" });
    const message = screen.getByText("Correct this description.");
    expect(description.getAttribute("aria-describedby")?.split(" ")).toContain(message.id);
    const group = screen.getByRole("group", { name: "Records" });
    expect(group.getAttribute("aria-describedby")?.split(" ")).toContain(screen.getByText("Review the records.").id);
    expect(screen.queryByText(/records:/)).toBeNull();
    const listLabel = screen.getAllByText("Lines")[0]!;
    expect(listLabel.closest(".md\\:col-span-2")).not.toBeNull();

    focusRef.mock.calls.at(-1)?.[0]?.focus();
    expect(document.activeElement).toBe(title);
    fireEvent.change(title, { target: { value: "Revised" } });
    expect(screen.getByRole("heading", { name: "Revised" })).toBeTruthy();
    expect(document.activeElement).toBe(title);
    fireEvent.change(description, { target: { value: "Corrected" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add item" })[0]!);

    const next = changes.mock.calls.at(-1)?.[0] as RowsValue;
    expect(next).toEqual([
      { ...retainedRow, title: "Revised", lines: [
        { ...retainedRow.lines[0], description: "Corrected" },
        { description: "" },
      ] },
      secondRow,
    ]);
    expect(next[1]).toBe(secondRow);
    expect(retainedRow.title).toBe("First");
  });

  test("renders titled rows read-only through the same widget without hidden controls", async () => {
    const [field] = sectionedFields();
    render(<AppRuntimeProvider runtime={{ widgets: sectionedWidgets }}>
      <LabeledDescriptorField field={field!}
        value={[{ identity: "row-1", title: "Retained", lines: [
          { identity: "line-1", fingerprint: "fingerprint-1", description: "Retained line" },
        ] }]} readOnly onChange={vi.fn()} />
    </AppRuntimeProvider>);

    expect(await screen.findByRole("heading", { name: "Retained" })).toBeTruthy();
    expect(await screen.findByText("Retained line")).toBeTruthy();
    expect(screen.queryByText("Identity")).toBeNull();
    expect(screen.queryByText("Fingerprint")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  test("edits nested fields and scalar/object lists through the native registry", async () => {
    const fields = structuredFields();
    const changes = vi.fn();
    renderStructured(fields[0]!, {
      title: "Draft",
      note: null,
      tags: ["first", "second"],
      tasks: [{ name: "One" }],
    }, changes, ["config.tasks.0.name: Name is invalid."]);

    const title = await screen.findByRole("textbox", { name: "Title" });
    expect(title.getAttribute("minlength")).toBe("3");
    fireEvent.change(title, { target: { value: "Ready" } });
    expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({ title: "Ready" }));
    expect(screen.getByText("Name is invalid.")).toBeTruthy();

    expect((screen.getAllByRole("button", { name: "Add item" })[1] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "Add item" })[0]!);
    expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({ tags: ["first", "second", ""] }));
    fireEvent.click(screen.getAllByRole("button", { name: "Move item 1 down" })[0]!);
    expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({ tags: ["second", "first"] }));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove item 1" })[0]!);
    expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({ tags: ["second"] }));
  });

  test("keeps omission distinct from null/default and removes all mutation controls when read-only", async () => {
    const fields = structuredFields();
    const initial = formSpecInitialValues(fields, { config: { tags: [], tasks: [] } });
    expect(initial).toEqual({ config: { title: "Untitled", note: null, tags: [], tasks: [] } });
    expect(normalizeFormSpecValues(fields, initial)).toEqual(initial);

    const changes = vi.fn();
    renderStructured(fields[0]!, initial.config, changes);
    expect(await screen.findByText("Left empty")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Not set" }));
    expect(changes).toHaveBeenLastCalledWith({ title: "Untitled", tags: [], tasks: [] });

    cleanup();
    renderStructured(fields[0]!, initial.config, changes, [], true);
    expect(await screen.findByText("Untitled")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  test("an explicit nullable integer can enter value mode without reapplying null", () => {
    function Harness() {
      const [value, setValue] = React.useState<unknown>(null);
      return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <LabeledDescriptorField
          field={{ name: "limit", label: "Limit", kind: "integer", widget: "integer", nullable: true }}
          value={value}
          onChange={setValue}
        />
      </AppRuntimeProvider>;
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Set value" }));
    const input = screen.getByRole("textbox", { name: "Limit" });
    expect(input).toBeTruthy();
    expect((input as HTMLInputElement).value).toBe("0");
    fireEvent.change(input, { target: { value: "42" } });
    expect((input as HTMLInputElement).value).toBe("42");
    expect(structuredFieldErrors(
      { name: "limit", kind: "integer", nullable: true },
      "",
      true,
    )).toEqual([{ path: "limit", keyword: "type", params: { type: "integer" } }]);
  });

  test("an empty text under a minimum length of one is missing unless the field is omittable or nullable, and under a minimum above one is too short", () => {
    expect(structuredFieldErrors({ name: "slug", minLength: 1, maxLength: 64 }, "", true))
      .toEqual([{ path: "slug", keyword: "required", params: {} }]);
    expect(structuredFieldErrors({ name: "nick", minLength: 1, omittable: true }, "", true))
      .toEqual([{ path: "nick", keyword: "minLength", params: { limit: 1 } }]);
    expect(structuredFieldErrors({ name: "caption", minLength: 1, nullable: true }, "", true))
      .toEqual([{ path: "caption", keyword: "minLength", params: { limit: 1 } }]);
    expect(structuredFieldErrors({ name: "handle", minLength: 3 }, "", true))
      .toEqual([{ path: "handle", keyword: "minLength", params: { limit: 3 } }]);
    expect(structuredFieldErrors({ name: "slug", minLength: 1 }, " ", true)).toEqual([]);
  });

  test("schema-resolved nested values retain presence checks without preempting constraint messages", () => {
    const [field] = deserializeFormSpec({ properties: {
      config: { type: "object", widget: "object", presenceRequired: true, properties: {
        title: { type: "string", presenceRequired: true, minLength: 3 },
        rows: { type: "array", widget: "list", presenceRequired: true, minItems: 3, items: {
          type: "object", widget: "object", properties: {
            note: { type: "string", presenceRequired: true, minLength: 3 },
          },
        } },
      } },
    } }, defaultWidgets);
    const value = { title: "no", rows: [{ note: "x" }, {}] };

    expect(structuredFieldErrors(field!, value, true)).toEqual([
      { path: "config.title", keyword: "minLength", params: { limit: 3 } },
      { path: "config.rows", keyword: "minItems", params: { limit: 3 } },
      { path: "config.rows.0.note", keyword: "minLength", params: { limit: 3 } },
      { path: "config.rows.1.note", keyword: "required", params: {} },
    ]);
    expect(structuredFieldErrors(field!, value, true, "config", false)).toEqual([
      { path: "config.rows.1.note", keyword: "required", params: {} },
    ]);
  });

  test("each failed rule comes with its keyword and limit at its path, members and list items included", () => {
    const [field] = deserializeFormSpec({ properties: {
      config: { type: "object", widget: "object", properties: {
        handle: { type: "string", minLength: 3 },
        code: { type: "string", maxLength: 4 },
        low: { type: "integer", minimum: 5 },
        high: { type: "number", maximum: 10 },
        count: { type: "integer", omittable: true },
        tags: { type: "array", widget: "list", minItems: 2, items: { type: "string", maxLength: 1 } },
        limits: { type: "array", widget: "list", maxItems: 1, items: { type: "string" } },
        token: { type: "string", presenceRequired: true },
        remark: { type: "string", presenceRequired: true },
        note: { type: "string", nullable: true, omittable: true },
      } },
    } }, defaultWidgets);

    expect(structuredFieldErrors(field!, {
      handle: "ab", code: "abcde", low: 2, high: 10.5, count: "seven", tags: ["xy"], limits: ["a", "b"], remark: null, note: null,
    }, true)).toEqual([
      { path: "config.handle", keyword: "minLength", params: { limit: 3 } },
      { path: "config.code", keyword: "maxLength", params: { limit: 4 } },
      { path: "config.low", keyword: "minimum", params: { limit: 5 } },
      { path: "config.high", keyword: "maximum", params: { limit: 10 } },
      { path: "config.count", keyword: "type", params: { type: "integer" } },
      { path: "config.tags", keyword: "minItems", params: { limit: 2 } },
      { path: "config.tags.0", keyword: "maxLength", params: { limit: 1 } },
      { path: "config.limits", keyword: "maxItems", params: { limit: 1 } },
      { path: "config.token", keyword: "required", params: {} },
      { path: "config.remark", keyword: "required", params: {} },
    ]);
  });

  test("a path reports one failed rule: presence, then type, then the lower bound before the upper", () => {
    const count = { name: "count", kind: "integer", presenceRequired: true, minimum: 5, maxLength: 1 } as const;
    expect(structuredFieldErrors(count, undefined, false)).toEqual([{ path: "count", keyword: "required", params: {} }]);
    expect(structuredFieldErrors(count, null, true)).toEqual([{ path: "count", keyword: "required", params: {} }]);
    expect(structuredFieldErrors(count, "12", true)).toEqual([{ path: "count", keyword: "type", params: { type: "integer" } }]);
    expect(structuredFieldErrors({ name: "code", minLength: 3, maxLength: 1 }, "ab", true))
      .toEqual([{ path: "code", keyword: "minLength", params: { limit: 3 } }]);
  });

  test("keeps an optional nullable Decimal union editable through a null transition", () => {
    const [field] = deserializeFormSpec({ properties: {
      line_total: {
        anyOf: [
          { type: "number" },
          { type: "string", pattern: "^(?!^[-+.]*$)[+-]?0*\\d*\\.?\\d*$" },
          { type: "null" },
        ],
        label: "Printed line total",
        widget: "float",
        omittable: true,
        default: null,
      },
    } }, defaultWidgets);
    expect(field).toMatchObject({
      kind: "any",
      widget: "float",
      nullable: true,
      omittable: true,
      hasDefault: true,
      defaultValue: null,
    });
    function Harness() {
      const [value, setValue] = React.useState<unknown>("69.95");
      return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <LabeledDescriptorField field={field!} value={value} onChange={setValue} />
      </AppRuntimeProvider>;
    }
    render(<Harness />);

    let input = screen.getByRole("textbox", { name: "Printed line total" });
    expect((input as HTMLInputElement).value).toBe("69.95");
    fireEvent.click(screen.getByRole("button", { name: "Leave empty" }));
    input = screen.getByRole("textbox", { name: "Printed line total" });
    expect(screen.getByText("Left empty")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
    fireEvent.change(input, { target: { value: "70.05" } });
    expect((input as HTMLInputElement).value).toBe("70.05");
    fireEvent.click(screen.getByRole("button", { name: "Leave empty" }));
    fireEvent.click(screen.getByRole("button", { name: "Omit value" }));
    input = screen.getByRole("textbox", { name: "Printed line total" });
    expect(screen.getByText("Optional")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
    fireEvent.change(input, { target: { value: "3180" } });
    expect((input as HTMLInputElement).value).toBe("3180");
  });

  test("rejects malformed persisted structured values instead of replacing them with empties", () => {
    const ObjectEdit = objectWidget.edit!;
    const ListEdit = listWidget.edit!;
    expect(() => render(<ObjectEdit value={[]} field={{ name: "config", objectTemplate: [] } as never} />))
      .toThrow('The "object" widget value must be an object.');
    expect(() => render(<ListEdit value={{}} field={{ name: "items", itemTemplate: { name: "item" } } as never} />))
      .toThrow('The "list" widget value must be an array.');
  });

  test("focuses the first editable structured child and an empty list's Add action", () => {
    const ObjectEdit = objectWidget.edit!;
    const ListEdit = listWidget.edit!;
    let focusRef = vi.fn<(target: { focus(): void } | null) => void>();
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ObjectEdit
        value={{ fixed: "read", editable: "write" }}
        field={{ name: "config", objectTemplate: [
          { name: "fixed", label: "Fixed", readOnly: true },
          { name: "editable", label: "Editable" },
        ] } as never}
        controlRef={focusRef}
      />
    </AppRuntimeProvider>);
    focusRef.mock.calls.at(-1)?.[0]?.focus();
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Editable" }));

    cleanup();
    focusRef = vi.fn<(target: { focus(): void } | null) => void>();
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ListEdit value={[]} field={{ name: "items", itemTemplate: { name: "item" } } as never}
        controlRef={focusRef} />
    </AppRuntimeProvider>);
    focusRef.mock.calls.at(-1)?.[0]?.focus();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add item" }));
  });

  test("retains the controlled dirty value across read-only revision rendering", async () => {
    const field = structuredFields()[0]!;
    function Harness() {
      const [value, setValue] = React.useState<unknown>({ title: "Draft", note: null, tags: [], tasks: [] });
      const [readOnly, setReadOnly] = React.useState(false);
      return <ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <button type="button" onClick={() => setReadOnly((current) => !current)}>Toggle revision</button>
        <LabeledDescriptorField field={field} value={value} readOnly={readOnly} onChange={setValue} />
      </AppRuntimeProvider></ModelMetadataProvider>;
    }
    render(<Harness />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Title" }), { target: { value: "Unsaved" } });
    fireEvent.click(screen.getByRole("button", { name: "Toggle revision" }));
    expect(await screen.findByText("Unsaved")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add item" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Toggle revision" }));
    expect((await screen.findByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("Unsaved");
  });
});

describe("one field's rules (fieldRuleErrors) and what counts as empty (isEmptyFieldValue)", () => {
  const requiredAt = (path: string) => ({ path, keyword: "required", params: {} });
  const ask = { required: true, validateConstraints: true };

  test("guard: null, undefined, a blank text and an empty list are empty; false, zero, a text, an object and a list holding a blank are not", () => {
    for (const value of [null, undefined, "", "  ", []]) expect(isEmptyFieldValue(value)).toBe(true);
    for (const value of [false, 0, "a", {}, [""]]) expect(isEmptyFieldValue(value)).toBe(false);
  });

  const missingEverywhere = (value: unknown, present: boolean) => {
    expect(fieldRuleErrors({ name: "code", maxLength: 8 }, { value, present }, ask)).toEqual([requiredAt("code")]);
    const auth: FieldDescriptor = { name: "auth", objectTemplate: [{ name: "token", required: true }] };
    expect(fieldRuleErrors(auth, { value: present ? { token: value } : {}, present: true }, { ...ask, required: false }))
      .toEqual([requiredAt("auth.token")]);
    const tags: FieldDescriptor = { name: "tags", itemTemplate: { name: "item", required: true } };
    if (present) expect(fieldRuleErrors(tags, { value: [value], present: true }, { ...ask, required: false })).toEqual([requiredAt("tags.0")]);
  };

  test("guard: a required value left out fails `required`: in a structured field and in a member", () => {
    missingEverywhere(undefined, false);
  });

  test.each<[string, unknown]>([
    ["a blank text", ""],
    ["a text of blanks", "  "],
    ["an empty list", []],
  ])("%s is a value in a required structured field, member or list item: it fails no rule, the model's own bounds judge it", (_label, value) => {
    expect(fieldRuleErrors({ name: "code", maxLength: 8 }, { value, present: true }, ask)).toEqual([]);
    const auth: FieldDescriptor = { name: "auth", objectTemplate: [{ name: "token", required: true, presenceRequired: true }] };
    expect(fieldRuleErrors(auth, { value: { token: value }, present: true }, { ...ask, required: false })).toEqual([]);
    const tags: FieldDescriptor = { name: "tags", itemTemplate: { name: "item", required: true } };
    expect(fieldRuleErrors(tags, { value: [value], present: true }, { ...ask, required: false })).toEqual([]);
  });

  test("an empty text under a minimum length of one, in a field neither omittable nor nullable, still reads as `required`, and an empty required list under a minimum of items says its bound", () => {
    expect(fieldRuleErrors({ name: "slug", required: true, presenceRequired: true, minLength: 1 }, { value: "", present: true }, ask))
      .toEqual([requiredAt("slug")]);
    expect(fieldRuleErrors({ name: "tags", required: true, presenceRequired: true, minItems: 1 }, { value: [], present: true }, ask))
      .toEqual([{ path: "tags", keyword: "minItems", params: { limit: 1 } }]);
  });

  test.each<[string, unknown]>([
    ["an own key holding undefined", undefined],
    ["a null the field does not allow", null],
  ])("guard: a required value that is %s fails `required`: in a structured field, in a member, and in a list item", (_label, value) => {
    missingEverywhere(value, true);
  });

  test("guard: a value a field that is not required may leave missing or blank fails nothing, at every depth", () => {
    const optional = { required: false, validateConstraints: true };
    expect(fieldRuleErrors({ name: "code", maxLength: 8 }, { value: undefined, present: false }, optional)).toEqual([]);
    expect(fieldRuleErrors({ name: "code", maxLength: 8 }, { value: " ", present: true }, optional)).toEqual([]);
    const auth: FieldDescriptor = { name: "auth", objectTemplate: [{ name: "token" }] };
    expect(fieldRuleErrors(auth, { value: {}, present: true }, optional)).toEqual([]);
    expect(fieldRuleErrors(auth, { value: { token: " " }, present: true }, optional)).toEqual([]);
  });

  test("a null in a field that is both required and nullable is a value, in a structured field, a member and a list item; absent, it is missing", () => {
    expect(fieldRuleErrors({ name: "note", presenceRequired: true, nullable: true }, { value: null, present: true }, ask)).toEqual([]);
    expect(fieldRuleErrors({ name: "note", presenceRequired: true, nullable: true }, { value: undefined, present: false }, ask))
      .toEqual([requiredAt("note")]);
    const auth: FieldDescriptor = { name: "auth", objectTemplate: [{ name: "token", required: true, nullable: true }] };
    expect(fieldRuleErrors(auth, { value: { token: null }, present: true }, { ...ask, required: false })).toEqual([]);
    expect(fieldRuleErrors(auth, { value: {}, present: true }, { ...ask, required: false })).toEqual([requiredAt("auth.token")]);
    const tags: FieldDescriptor = { name: "tags", itemTemplate: { name: "item", required: true, nullable: true } };
    expect(fieldRuleErrors(tags, { value: [null], present: true }, { ...ask, required: false })).toEqual([]);
  });

  test("guard: a required field with no presence flag, template or bound fails `required` for a null, even where it allows one", () => {
    expect(fieldRuleErrors({ name: "note", nullable: true }, { value: null, present: true }, ask)).toEqual([requiredAt("note")]);
  });

  test.each<[string, FieldDescriptor, unknown]>([
    ["a hidden member left out", { name: "auth", objectTemplate: [{ name: "token", required: true, presenceRequired: true, hidden: true }] }, {}],
    ["a read-only member left out", { name: "auth", objectTemplate: [{ name: "token", required: true, presenceRequired: true, readOnly: true }] }, {}],
    ["a member left out under a hidden member", { name: "auth", objectTemplate: [
      { name: "inner", hidden: true, objectTemplate: [{ name: "token", required: true, presenceRequired: true }] },
    ] }, { inner: {} }],
    ["a member left out under a read-only member", { name: "auth", objectTemplate: [
      { name: "inner", readOnly: true, objectTemplate: [{ name: "token", required: true, presenceRequired: true }] },
    ] }, { inner: {} }],
    ["a read-only member holding a value that breaks a bound", { name: "auth", objectTemplate: [{ name: "code", maxLength: 2, readOnly: true }] }, { code: "abcd" }],
    ["a hidden list item holding a blank text", { name: "tags", itemTemplate: { name: "item", required: true, hidden: true } }, [" "]],
    ["a read-only list item holding a blank text", { name: "tags", itemTemplate: { name: "item", required: true, readOnly: true } }, [" "]],
  ])("%s is not asked: no rule at its path or under it, the person cannot change it", (_label, field, value) => {
    expect(fieldRuleErrors(field, { value, present: true }, { required: false, validateConstraints: true })).toEqual([]);
  });

  test.fails("defect: a member that is neither required nor presence-required, holding an own key with undefined, fails `required`, though a field holding one is absent; it should fail nothing", () => {
    const auth: FieldDescriptor = { name: "auth", objectTemplate: [{ name: "nick", maxLength: 8 }] };
    expect(fieldRuleErrors(auth, { value: { nick: undefined }, present: true }, { required: false, validateConstraints: true })).toEqual([]);
  });

  test("guard: an empty object is a value, in a required field and in a required member", () => {
    expect(fieldRuleErrors({ name: "config", objectTemplate: [] }, { value: {}, present: true }, ask)).toEqual([]);
    const outer: FieldDescriptor = { name: "outer", objectTemplate: [{ name: "inner", required: true, objectTemplate: [] }] };
    expect(fieldRuleErrors(outer, { value: { inner: {} }, present: true }, { ...ask, required: false })).toEqual([]);
    expect(fieldRuleErrors({ name: "config", widget: "json" }, { value: {}, present: true }, ask)).toEqual([]);
  });

  test("a path reports one rule: presence and `required`, then a number's type, then the lower bound before the upper", () => {
    const count: FieldDescriptor = { name: "count", kind: "integer", minimum: 5, maximum: 3 };
    expect(fieldRuleErrors(count, { value: undefined, present: false }, ask)).toEqual([requiredAt("count")]);
    expect(fieldRuleErrors(count, { value: "", present: true }, ask)).toEqual([{ path: "count", keyword: "type", params: { type: "integer" } }]);
    expect(fieldRuleErrors(count, { value: "7", present: true }, ask)).toEqual([{ path: "count", keyword: "type", params: { type: "integer" } }]);
    expect(fieldRuleErrors(count, { value: 4, present: true }, ask)).toEqual([{ path: "count", keyword: "minimum", params: { limit: 5 } }]);
    expect(fieldRuleErrors(count, { value: 6, present: true }, ask)).toEqual([{ path: "count", keyword: "maximum", params: { limit: 3 } }]);
  });

  test("the surface decides whether the field is required at its own path, and each member answers to its own flag", () => {
    const auth: FieldDescriptor = { name: "auth", required: true, objectTemplate: [{ name: "token", required: true, maxLength: 2 }] };
    expect(fieldRuleErrors(auth, { value: undefined, present: false }, { required: false, validateConstraints: true })).toEqual([]);
    expect(fieldRuleErrors(auth, { value: undefined, present: false }, ask)).toEqual([requiredAt("auth")]);
    expect(fieldRuleErrors(auth, { value: {}, present: true }, { required: false, validateConstraints: true }))
      .toEqual([requiredAt("auth.token")]);
  });

  test("guard: with constraints off, only presence is checked: a missing required value fails `required`, and a blank text, an empty list and a broken bound fail nothing", () => {
    const auth: FieldDescriptor = { name: "auth", objectTemplate: [{ name: "token", required: true, maxLength: 2 }] };
    const resolved = { required: false, validateConstraints: false };
    expect(fieldRuleErrors(auth, { value: {}, present: true }, resolved)).toEqual([requiredAt("auth.token")]);
    expect(fieldRuleErrors(auth, { value: { token: null }, present: true }, resolved)).toEqual([requiredAt("auth.token")]);
    expect(fieldRuleErrors(auth, { value: { token: " " }, present: true }, resolved)).toEqual([]);
    expect(fieldRuleErrors(auth, { value: { token: "abc" }, present: true }, resolved)).toEqual([]);
    expect(fieldRuleErrors({ name: "tags", minItems: 1 }, { value: [], present: true }, { required: true, validateConstraints: false })).toEqual([]);
  });
});

function SectionedEdit(props: WidgetRenderProps<RowsValue>) {
  return <RowsField {...props} rowTitle={(row) => String(row.title)} />;
}

const sectionedWidgets = {
  ...defaultWidgets,
  sectionedRows: {
    acceptsRowTemplate: true as const,
    edit: SectionedEdit,
    read: (props: WidgetRenderProps<RowsValue>) => <SectionedEdit {...props} readOnly />,
  },
};

function sectionedFields() {
  return deserializeFormSpec({ properties: {
    records: { type: "array", label: "Records", widget: "sectionedRows", items: {
      type: "object", properties: {
        identity: { type: "string", label: "Identity", hidden: true },
        title: { type: "string", label: "Title" },
        lines: { type: "array", label: "Lines", widget: "list", items: {
          type: "object", widget: "object", properties: {
            identity: { type: "string", label: "Identity", hidden: true, omittable: true },
            fingerprint: { type: "string", label: "Fingerprint", hidden: true, omittable: true },
            description: { type: "string", label: "Description" },
          },
        } },
      },
    } },
  } }, sectionedWidgets);
}

function structuredFields() {
  return deserializeFormSpec({
    type: "object",
    required: ["config"],
    properties: {
      config: {
        type: "object", widget: "object", presenceRequired: true, required: ["title", "tags", "tasks"],
        properties: {
          title: { type: "string", label: "Title", presenceRequired: true, defaultValue: "Untitled", minLength: 3 },
          note: { type: "string", label: "Note", nullable: true, omittable: true, defaultValue: null },
          tags: { type: "array", widget: "list", label: "Tags", presenceRequired: true, minItems: 1, items: { type: "string", label: "Tag" } },
          tasks: { type: "array", widget: "list", label: "Tasks", presenceRequired: true, maxItems: 1, items: {
            type: "object", widget: "object", required: ["name"], properties: { name: { type: "string", label: "Name" } },
          } },
        },
      },
    },
  }, defaultWidgets);
}

function renderStructured(field: ReturnType<typeof structuredFields>[number], value: unknown, onChange: (value: unknown) => void, messages: readonly string[] = [], readOnly = false) {
  return render(<ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <LabeledDescriptorField field={field} value={value} messages={messages} readOnly={readOnly} onChange={onChange} />
  </AppRuntimeProvider></ModelMetadataProvider>);
}
