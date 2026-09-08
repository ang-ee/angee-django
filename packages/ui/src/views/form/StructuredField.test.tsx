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

const metadata = schemaFieldMetadataFromDataResources([]);

describe("structured FormSpec widgets", () => {
  afterEach(cleanup);

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

  test("rejects malformed persisted structured values instead of replacing them with empties", () => {
    const ObjectEdit = objectWidget.edit!;
    const ListEdit = listWidget.edit!;
    expect(() => render(<ObjectEdit value={[]} field={{ name: "config", objectTemplate: [] } as never} />))
      .toThrow('The "object" widget value must be an object.');
    expect(() => render(<ListEdit value={{}} field={{ name: "items", itemTemplate: { name: "item" } } as never} />))
      .toThrow('The "list" widget value must be an array.');
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
