// @vitest-environment happy-dom

import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { deserializeFormSpec, formSpecInitialValues, normalizeFormSpecValues } from "./form-spec";
import { LabeledDescriptorField } from "./MutationDialog";
import { listWidget, objectWidget } from "./StructuredField";
import { structuredFieldErrorPaths } from "./field-values";
import type { WidgetRenderProps } from "../../widgets/types";
import { RowsField, type RowsValue } from "./RowsField";

const metadata = schemaFieldMetadataFromDataResources([]);

describe("structured FormSpec widgets", () => {
  afterEach(cleanup);

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
          field={{ ...field!, widget: "sectionedRows" }}
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
    expect(listLabel.parentElement?.parentElement?.className).toContain("md:col-span-2");

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
      <LabeledDescriptorField field={{ ...field!, widget: "sectionedRows" }}
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
    fireEvent.change(input, { target: { value: "42" } });
    expect((input as HTMLInputElement).value).toBe("42");
    expect(structuredFieldErrorPaths(
      { name: "limit", kind: "integer", nullable: true },
      "",
      true,
    )).toEqual(["limit"]);
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

function SectionedEdit(props: WidgetRenderProps<RowsValue>) {
  return <RowsField {...props} rowTitle={(row) => String(row.title)} />;
}

const sectionedWidgets = {
  ...defaultWidgets,
  sectionedRows: {
    edit: SectionedEdit,
    read: (props: WidgetRenderProps<RowsValue>) => <SectionedEdit {...props} readOnly />,
  },
};

function sectionedFields() {
  return deserializeFormSpec({ properties: {
    records: { type: "array", label: "Records", widget: "rows", items: {
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
  } }, defaultWidgets);
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
