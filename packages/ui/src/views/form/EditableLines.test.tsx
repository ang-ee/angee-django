// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useForm, type UseFormReturn } from "react-hook-form";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources, type DataResourceLinesMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, describe, expect, test } from "vitest";

import { ModalsHost } from "../../feedback";
import { testDndTransfer } from "../../lib/dnd-test-fixtures";
import { CLIENT_LINE_KEY } from "./editable-lines";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets, type WidgetRenderProps } from "../../widgets";
import { EditableLines } from "./EditableLines";
import type { ValidationErrors } from "./validation-errors";

const LINES = {
  field: "lines",
  modelLabel: "demo.Line",
  positionField: "position",
  fields: [
    { name: "label", scalar: "String", requiredOnCreate: true },
    { name: "quantity", scalar: "Decimal", requiredOnCreate: false },
    { name: "position", scalar: "Int", requiredOnCreate: false },
  ].map((field) => ({
    ...field, kind: "scalar" as const, readable: true, filterable: false,
    sortable: false, aggregatable: false, groupable: false, creatable: true, updatable: true,
  })),
} satisfies DataResourceLinesMetadata;

function Host({
  footer,
  inspectContext = false,
  compact = false,
  readOnly = false,
  empty = false,
  rowErrors,
}: {
  inspectContext?: boolean;
  compact?: boolean;
  readOnly?: boolean;
  empty?: boolean;
  rowErrors?: readonly ValidationErrors[];
  footer?: (rows: readonly Record<string, unknown>[]) => React.ReactNode;
}): React.ReactElement {
  const form = useForm<Record<string, unknown>>({
    defaultValues: {
      lines: empty ? [] : [
        { id: "one", label: "Widget", quantity: 2, amount_subtotal: "20.00", position: 0 },
        { id: "two", label: "Gadget", quantity: 5, amount_subtotal: "25.00", position: 1 },
      ],
    },
  });
  const contextWidget = {
    read: ({ row, parentRow, field }: WidgetRenderProps) => (
      <span>
        {String((row as { label: string }).label)} / {String((parentRow as { company: string }).company)} / {field?.controlProps?.presentation}
      </span>
    ),
  };
  const lines = inspectContext
    ? {
        ...LINES,
        fields: LINES.fields.map((field) =>
          field.name === "label" ? { ...field, widget: "demo.lines.context" } : field,
        ),
      }
    : LINES;
  return (
    <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, "demo.lines.context": contextWidget } }}>
      <ModalsHost><EditableLines
        control={form.control}
        setValue={form.setValue}
        name="lines"
        lines={lines}
        parentRow={{ company: "Acme" }}
        footer={footer}
        readOnly={readOnly}
        rowErrors={rowErrors}
        primaryFields={compact ? ["label"] : undefined}
        supplementalColumns={compact ? [{
          key: "subtotal",
          header: "Subtotal", minWidth: 144,
          render: (row, _parent, _index, { formIsDirty }) => (
            <span>{formIsDirty ? "Pending save" : String(row.amount_subtotal)}</span>
          ),
        }] : undefined}
      /></ModalsHost>
    </AppRuntimeProvider>
  );
}

afterEach(cleanup);

function rowPatchFixture() {
  let form!: UseFormReturn<Record<string, unknown>>;
  const callbacks = new Map<string, NonNullable<WidgetRenderProps["onRowChange"]>>();
  const metadata = schemaFieldMetadataFromDataResources([testDataResource("demo.Product")]);
  const lines: DataResourceLinesMetadata = { ...LINES, fields: [
    { ...LINES.fields[0]!, name: "product", kind: "relation", scalar: null, relationModelLabel: "demo.Product", widget: "demo.product" },
    ...LINES.fields,
  ] };
  const productWidget = {
    read: ({ row }: WidgetRenderProps) => <span>Locked {String((row as { label: string }).label)}</span>,
    edit: ({ row, onRowChange }: WidgetRenderProps) => {
      const label = String((row as { label: string }).label);
      return <button type="button" onClick={() => callbacks.set(label, onRowChange!)}>Preview {label}</button>;
    },
  };
  function PatchHost({ readOnly = false }: { readOnly?: boolean }) {
    form = useForm<Record<string, unknown>>({ defaultValues: { lines: [
      { id: "one", label: "Widget", quantity: 2, position: 0 },
      { id: "two", label: "Gadget", quantity: 5, position: 1 },
    ] } });
    return <ModelMetadataProvider metadata={metadata}>
      <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, "demo.product": productWidget } }}>
        <ModalsHost><EditableLines
          control={form.control}
          setValue={form.setValue}
          name="lines"
          lines={lines}
          readOnly={readOnly}
        /></ModalsHost>
      </AppRuntimeProvider>
    </ModelMetadataProvider>;
  }
  const view = render(<PatchHost />);
  return { form: () => form, callbacks, lock: () => view.rerender(<PatchHost readOnly />), unmount: view.unmount };
}

async function toggleField(label: string) {
  fireEvent.click(screen.getByRole("button", { name: "Visible fields" }));
  const item = await screen.findByRole("menuitemcheckbox", { name: label });
  fireEvent.click(item);
  fireEvent.keyDown(item, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
}

describe("EditableLines", () => {
  test("list-owned drag reorder keeps pending patches bound to the same child", () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Widget" }));
    const handles = screen.getAllByRole("button", { name: "Reorder row" });
    const dataTransfer = testDndTransfer();
    fireEvent.dragStart(handles[0]!, { dataTransfer });
    fireEvent.drop(handles[1]!.closest("tr")!, { dataTransfer });
    expect((f.form().getValues("lines") as { id: string }[]).map((row) => row.id)).toEqual(["two", "one"]);
    act(() => f.callbacks.get("Widget")!({ label: "Resolved after reorder" }));
    expect(f.form().getValues("lines.1")).toMatchObject({ id: "one", label: "Resolved after reorder" });
    fireEvent.keyDown(screen.getAllByRole("button", { name: "Reorder row" })[1]!, { key: "ArrowUp", altKey: true });
    expect(f.form().getValues("lines.0.id")).toBe("one");
  });

  test("shared row actions duplicate live values with a new client identity and remove the duplicate", async () => {
    const f = rowPatchFixture();
    act(() => f.form().setValue("lines.0.quantity" as never, 8 as never, { shouldDirty: true }));
    await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: "Duplicate line" })[0]!); });
    const duplicate = f.form().getValues("lines.1" as never) as unknown as Record<string, unknown>;
    expect(duplicate).toMatchObject({ label: "Widget", quantity: 8 });
    expect(duplicate.id).toBeUndefined();
    expect(typeof duplicate[CLIENT_LINE_KEY]).toBe("string");
    await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: "Remove line" })[1]!); });
    expect((f.form().getValues("lines") as { id: string }[]).map((row) => row.id)).toEqual(["one", "two"]);
  });

  test("a custom relation widget patches its stable row after earlier deletion and preserves later sibling edits", async () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    await act(async () => { fireEvent.click(screen.getAllByLabelText("Remove line")[0]!); });
    act(() => f.form().setValue<string>("lines.0.quantity", 8, { shouldDirty: true }));
    act(() => f.callbacks.get("Gadget")!({ product: { id: "new-product", name: "New" }, label: "New label" }));
    expect(f.form().getValues("lines")).toEqual([
      { id: "two", label: "New label", quantity: 8, position: 1, product: { id: "new-product", name: "New" } },
    ]);
    expect(f.form().getFieldState("lines").isDirty).toBe(true);
  });

  test("pending row patches cannot recreate a deleted line or alter a read-only form", async () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    await act(async () => { fireEvent.click(screen.getAllByLabelText("Remove line")[0]!); });
    act(() => f.callbacks.get("Widget")!({ label: "Resurrected" }));
    expect(f.form().getValues("lines")).toMatchObject([{ id: "two", label: "Gadget" }]);
    f.lock();
    expect(screen.getByText("Locked Gadget")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add line" })).toBeNull();
    act(() => f.callbacks.get("Gadget")!({ label: "Forbidden" }));
    expect(f.form().getValues("lines")).toMatchObject([{ id: "two", label: "Gadget" }]);
    f.unmount();
    act(() => f.callbacks.get("Gadget")!({ label: "After unmount" }));
    expect(f.form().getValues("lines")).toMatchObject([{ id: "two", label: "Gadget" }]);
  });

  test("independent pending row patches both reach their still-mounted line", () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    const pending = f.callbacks.get("Gadget")!;
    act(() => pending({ product: { id: "new-product", name: "New" } }));
    act(() => pending({ label: "Second independent result" }));
    expect(f.form().getValues("lines.1")).toMatchObject({
      product: { id: "new-product", name: "New" },
      label: "Second independent result",
    });
  });

  test("completing a row patch preserves focus in another cell", () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    const pending = f.callbacks.get("Gadget")!;
    const focusTarget = screen.getAllByRole("textbox", { name: "Quantity" })[1]!;
    focusTarget.focus();
    expect(document.activeElement).toBe(focusTarget);
    act(() => pending({ label: "Resolved preview" }));
    expect(document.activeElement).toBe(focusTarget);
  });

  test("a same-event cell change survives an accompanying sibling patch", () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    const pending = f.callbacks.get("Gadget")!;
    act(() => {
      f.form().setValue("lines.1.quantity" as never, 8 as never, { shouldDirty: true });
      pending({ label: "Patched label" });
    });
    expect(f.form().getValues("lines.1.quantity")).toBe(8);
  });

  test("passes the live child and owning document to a registered widget", () => {
    render(<Host inspectContext />);
    expect(screen.getByText("Widget / Acme / cell")).toBeTruthy();
    expect(screen.getByText("Gadget / Acme / cell")).toBeTruthy();
  });
  test("renders one editable cell row per seeded line, hiding the position column", () => {
    render(<Host />);

    expect(screen.getByDisplayValue("Widget")).toBeTruthy();
    expect(screen.getByDisplayValue("Gadget")).toBeTruthy();
    // A drag handle per row; the `position` column renders no header/cell.
    expect(screen.getAllByLabelText("Reorder row")).toHaveLength(2);
    expect(screen.queryByText("Position")).toBeNull();
    expect(screen.getByText("Label")).toBeTruthy();
    expect(screen.getByText("Quantity")).toBeTruthy();
    expect(screen.getAllByRole("textbox", { name: "Label" })).toHaveLength(2);
    expect(screen.getAllByRole("textbox", { name: "Quantity" })).toHaveLength(2);
    expect(screen.getByRole("table").tagName).toBe("TABLE");
    expect(screen.getByRole("table").closest("[data-resource-presentation]")?.getAttribute("data-resource-presentation")).toBe("embedded");
    const table = within(screen.getByRole("table"));
    const rows = table.getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getAllByRole("columnheader").map((header) => header.textContent))
      .toEqual(["Reorder row", "Label", "Quantity", "Actions"]);
    expect(within(rows[1]!).getAllByRole("cell")).toHaveLength(4);
    expect(within(rows.at(-1)!).getByRole("button", { name: "Add line" }).closest("tfoot")).toBeTruthy();
    expect(table.getByRole("columnheader", { name: /^Quantity/ }).className).toContain("text-right");
    expect(table.getAllByRole("textbox", { name: "Label" })[0]!.className).toContain("h-btn-sm");
  });

  test("keeps the header and final add row when empty, and uses the same table for plain read-only values", () => {
    const view = render(<Host empty />);
    expect(screen.getByRole("columnheader", { name: "Label" })).toBeTruthy();
    expect(screen.getByText("No lines yet.")).toBeTruthy();
    expect(within(screen.getAllByRole("row").at(-1)!).getByRole("button", { name: "Add line" })).toBeTruthy();
    view.unmount();
    render(<Host readOnly />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("cell", { name: "Widget" })).toBeTruthy();
    // Read-only keeps the trailing header for the fields menu, with no verbs in it.
    expect(screen.queryByRole("columnheader", { name: "Reorder row" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Duplicate line" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove line" })).toBeNull();
    expect(within(screen.getByRole("table")).queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reorder row" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add line" })).toBeNull();
  });

  test("retains invalid cell chrome and messages in the plain presentation", () => {
    render(<Host rowErrors={[{ fieldErrors: { quantity: ["Must be positive"] }, formErrors: [] }]} />);
    const input = screen.getAllByRole("textbox", { name: "Quantity" })[0]!;
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.parentElement?.className).toContain("border-danger");
    expect(screen.getByText("Must be positive")).toBeTruthy();
  });

  test("the header's visible-fields menu chooses optional columns without losing their values", async () => {
    render(<Host compact />);
    expect(screen.queryByRole("columnheader", { name: /^Quantity/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Show line details" })).toBeNull();
    const subtotal = screen.getByRole("columnheader", { name: /^Subtotal/ });
    expect(subtotal.className).toContain("text-right");
    expect(subtotal.style.minWidth).toBe("144px");
    expect(subtotal.contains(screen.getByRole("button", { name: "Visible fields" }))).toBe(false);
    expect(screen.getByText("20.00")).toBeTruthy();
    await toggleField("Quantity");
    expect(screen.getAllByRole("textbox", { name: "Quantity" }).map((input) => (input as HTMLInputElement).value))
      .toEqual(["2", "5"]);
    await toggleField("Quantity");
    expect(screen.queryByRole("columnheader", { name: /^Quantity/ })).toBeNull();
    await toggleField("Quantity");
    expect(screen.getAllByRole("textbox", { name: "Quantity" })).toHaveLength(2);
  });

  test("server validation reveals a manually hidden optional column and keeps its cell message", async () => {
    const view = render(<Host compact />);
    await toggleField("Quantity");
    await toggleField("Quantity");
    view.rerender(<Host compact rowErrors={[{ fieldErrors: { quantity: ["Must be positive"] }, formErrors: [] }]} />);
    expect(screen.getByText("Must be positive")).toBeTruthy();
    expect(screen.getAllByRole("textbox", { name: "Quantity" })[0]!.getAttribute("aria-invalid")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Visible fields" }));
    expect((await screen.findByRole("menuitemcheckbox", { name: "Quantity" })).getAttribute("aria-disabled")).toBe("true");
  });

  test("lets a supplemental projection hide stale saved values while the form is dirty", () => {
    render(<Host compact />);
    fireEvent.change(screen.getAllByRole("textbox", { name: "Label" })[0]!, {
      target: { value: "Changed widget" },
    });
    expect(screen.getAllByText("Pending save")).toHaveLength(2);
    expect(screen.queryByText("20.00")).toBeNull();
  });

  test("adds a blank row and removes a row", async () => {
    render(<Host />);

    fireEvent.click(screen.getByRole("button", { name: "Add line" }));
    expect(screen.getAllByLabelText("Reorder row")).toHaveLength(3);

    await act(async () => { fireEvent.click(screen.getAllByLabelText("Remove line")[0]!); });
    expect(screen.getAllByLabelText("Reorder row")).toHaveLength(2);
  });

  test("renders the composer's footer with the live rows", () => {
    render(<Host footer={(rows) => <div>lines: {rows.length}</div>} />);
    expect(screen.getByText("lines: 2")).toBeTruthy();
    expect(screen.getByRole("table").contains(screen.getByText("lines: 2"))).toBe(false);
    expect(screen.getByRole("button", { name: "Add line" }).compareDocumentPosition(screen.getByText("lines: 2")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("a locked row keeps its place with read-only locked cells while its other cells stay editable", () => {
    render(<LockedHost />);

    const table = within(screen.getByRole("table"));
    const [, system, free] = table.getAllByRole("row");
    // The list shows a lock where the system row's handle would be; the row carries
    // the System marker and offers no duplicate or remove. The ordinary row keeps all three.
    expect(within(system!).getByRole("img", { name: "Locked row" })).toBeTruthy();
    expect(within(system!).queryByRole("button", { name: "Reorder row" })).toBeNull();
    expect(within(system!).getByText("System")).toBeTruthy();
    expect(within(system!).queryByRole("button", { name: "Duplicate line" })).toBeNull();
    expect(within(system!).queryByRole("button", { name: "Remove line" })).toBeNull();
    expect(within(free!).getByRole("button", { name: "Reorder row" })).toBeTruthy();
    expect(within(free!).getByRole("button", { name: "Remove line" })).toBeTruthy();
    expect(within(free!).queryByText("System")).toBeNull();
    // Its locked label reads; its unlocked quantity still edits.
    expect(within(system!).queryByRole("textbox", { name: "Label" })).toBeNull();
    expect(within(system!).getByText("Triage")).toBeTruthy();
    expect(within(system!).getByRole("textbox", { name: "Quantity" })).toBeTruthy();
    expect(within(free!).getByRole("textbox", { name: "Label" })).toBeTruthy();
  });

  test("removing and adding rows never touches the locked row", async () => {
    render(<LockedHost />);

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Remove line" })); });
    expect(screen.getByText("Triage")).toBeTruthy();
    expect(screen.queryByDisplayValue("Gadget")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add line" }));
    // A new row is never locked: its label edits and it can be reordered.
    expect(screen.getAllByRole("textbox", { name: "Label" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Reorder row" })).toHaveLength(1);
    expect(screen.getAllByRole("img", { name: "Locked row" })).toHaveLength(1);
  });

  test("lines without a lock field add no System column", () => {
    render(<Host />);
    expect(screen.queryByRole("columnheader", { name: "System" })).toBeNull();
    expect(screen.queryByRole("img", { name: "Locked row" })).toBeNull();
  });
});

describe("EditableLines authored fields", () => {
  test("authored headers name the columns and cells, and their help explains a column kept in the header menu", async () => {
    function AuthoredHost(): React.ReactElement {
      const form = useForm<Record<string, unknown>>({
        defaultValues: { lines: [{ id: "one", label: "Widget", quantity: 2, position: 0 }] },
      });
      return (
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <ModalsHost><EditableLines
            control={form.control}
            setValue={form.setValue}
            name="lines"
            lines={LINES}
            primaryFields={["label"]}
            fields={[
              { name: "label", label: "Item" },
              { name: "quantity", label: "Units", description: "How many the order ships." },
            ]}
          /></ModalsHost>
        </AppRuntimeProvider>
      );
    }
    render(<AuthoredHost />);

    expect(screen.getByRole("columnheader", { name: /^Item/ })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Item" })).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: /^Units/ })).toBeNull();
    expect(screen.getByText("How many the order ships.")).toBeTruthy();
    await toggleField("Units");
    expect(screen.getByRole("textbox", { name: "Units" })).toBeTruthy();
  });

  test("lines without authored help render no legend beside the composer's footer", () => {
    const { container } = render(<Host footer={(rows) => <div>lines: {rows.length}</div>} />);
    expect(container.querySelector("dl")).toBeNull();
    expect(screen.getByText("lines: 2")).toBeTruthy();
  });
});

function LockedHost(): React.ReactElement {
  const form = useForm<Record<string, unknown>>({
    defaultValues: {
      lines: [
        { id: "system", label: "Triage", quantity: 1, position: 0, locked_fields: ["label"] },
        { id: "free", label: "Gadget", quantity: 5, position: 1, locked_fields: [] },
      ],
    },
  });
  return (
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ModalsHost><EditableLines
        control={form.control}
        setValue={form.setValue}
        name="lines"
        lines={{ ...LINES, lockField: "locked_fields" }}
      /></ModalsHost>
    </AppRuntimeProvider>
  );
}
