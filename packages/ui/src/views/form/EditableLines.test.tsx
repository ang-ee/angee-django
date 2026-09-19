// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useForm, type UseFormReturn } from "react-hook-form";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources, type DataResourceLinesMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets, type WidgetRenderProps } from "../../widgets";
import { EditableLines } from "./EditableLines";

const LINES = {
  field: "lines",
  modelLabel: "demo.Line",
  positionField: "position",
  fields: [
    {
      name: "label",
      kind: "scalar",
      scalar: "String",
      readable: true,
      filterable: false,
      sortable: false,
      aggregatable: false,
      groupable: false,
      creatable: true,
      updatable: true,
      requiredOnCreate: true,
    },
    {
      name: "quantity",
      kind: "scalar",
      scalar: "Decimal",
      readable: true,
      filterable: false,
      sortable: false,
      aggregatable: false,
      groupable: false,
      creatable: true,
      updatable: true,
      requiredOnCreate: false,
    },
    {
      name: "position",
      kind: "scalar",
      scalar: "Int",
      readable: true,
      filterable: false,
      sortable: false,
      aggregatable: false,
      groupable: false,
      creatable: true,
      updatable: true,
      requiredOnCreate: false,
    },
  ],
} satisfies DataResourceLinesMetadata;

function Host({
  footer,
  inspectContext = false,
  compact = false,
}: {
  inspectContext?: boolean;
  compact?: boolean;
  footer?: (rows: readonly Record<string, unknown>[]) => React.ReactNode;
}): React.ReactElement {
  const form = useForm<Record<string, unknown>>({
    defaultValues: {
      lines: [
        { id: "one", label: "Widget", quantity: 2, amount_subtotal: "20.00", position: 0 },
        { id: "two", label: "Gadget", quantity: 5, amount_subtotal: "25.00", position: 1 },
      ],
    },
  });
  const contextWidget = {
    read: ({ row, parentRow }: WidgetRenderProps) => (
      <span>
        {String((row as { label: string }).label)} / {String((parentRow as { company: string }).company)}
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
      <EditableLines
        control={form.control}
        setValue={form.setValue}
        name="lines"
        lines={lines}
        parentRow={{ company: "Acme" }}
        footer={footer}
        primaryFields={compact ? ["label"] : undefined}
        supplementalColumns={compact ? [{
          key: "subtotal",
          header: "Subtotal",
          render: (row, _parent, _index, { formIsDirty }) => (
            <span>{formIsDirty ? "Pending save" : String(row.amount_subtotal)}</span>
          ),
        }] : undefined}
      />
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
        <EditableLines
          control={form.control}
          setValue={form.setValue}
          name="lines"
          lines={lines}
          readOnly={readOnly}
        />
      </AppRuntimeProvider>
    </ModelMetadataProvider>;
  }
  const view = render(<PatchHost />);
  return { form: () => form, callbacks, lock: () => view.rerender(<PatchHost readOnly />), unmount: view.unmount };
}

describe("EditableLines", () => {
  test("a custom relation widget patches its stable row after earlier deletion and preserves later sibling edits", () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    fireEvent.click(screen.getAllByLabelText("Remove line")[0]!);
    act(() => f.form().setValue<string>("lines.0.quantity", 8, { shouldDirty: true }));
    act(() => f.callbacks.get("Gadget")!({ product: { id: "new-product", name: "New" }, label: "New label" }));
    expect(f.form().getValues("lines")).toEqual([
      { id: "two", label: "New label", quantity: 8, position: 1, product: { id: "new-product", name: "New" } },
    ]);
    expect(f.form().getFieldState("lines").isDirty).toBe(true);
  });

  test("pending row patches cannot recreate a deleted line or alter a read-only form", () => {
    const f = rowPatchFixture();
    fireEvent.click(screen.getByRole("button", { name: "Preview Widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview Gadget" }));
    fireEvent.click(screen.getAllByLabelText("Remove line")[0]!);
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
    expect(screen.getByText("Widget / Acme")).toBeTruthy();
    expect(screen.getByText("Gadget / Acme")).toBeTruthy();
  });
  test("renders one editable cell row per seeded line, hiding the position column", () => {
    render(<Host />);

    expect(screen.getByDisplayValue("Widget")).toBeTruthy();
    expect(screen.getByDisplayValue("Gadget")).toBeTruthy();
    // A drag handle per row; the `position` column renders no header/cell.
    expect(screen.getAllByLabelText("Reorder line")).toHaveLength(2);
    expect(screen.queryByText("Position")).toBeNull();
    expect(screen.getByText("Label")).toBeTruthy();
    expect(screen.getByText("Quantity")).toBeTruthy();
    expect(screen.getAllByRole("textbox", { name: "Label" })).toHaveLength(2);
    expect(screen.getAllByRole("textbox", { name: "Quantity" })).toHaveLength(2);
  });

  test("keeps advanced values available behind details and renders read-only projections", () => {
    render(<Host compact />);

    expect(screen.queryByText("Quantity")).toBeNull();
    expect(screen.getByText("Subtotal")).toBeTruthy();
    expect(screen.getByText("20.00")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show line details" }));
    expect(screen.getByText("Quantity")).toBeTruthy();
    expect(screen.getAllByRole("textbox", { name: "Quantity" })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Hide line details" }));
    expect(screen.queryByText("Quantity")).toBeNull();
  });

  test("lets a supplemental projection hide stale saved values while the form is dirty", () => {
    render(<Host compact />);
    fireEvent.change(screen.getAllByRole("textbox", { name: "Label" })[0]!, {
      target: { value: "Changed widget" },
    });
    expect(screen.getAllByText("Pending save")).toHaveLength(2);
    expect(screen.queryByText("20.00")).toBeNull();
  });

  test("adds a blank row and removes a row", () => {
    render(<Host />);

    fireEvent.click(screen.getByRole("button", { name: "Add line" }));
    expect(screen.getAllByLabelText("Reorder line")).toHaveLength(3);

    fireEvent.click(screen.getAllByLabelText("Remove line")[0]!);
    expect(screen.getAllByLabelText("Reorder line")).toHaveLength(2);
  });

  test("renders the composer's footer with the live rows", () => {
    render(<Host footer={(rows) => <div>lines: {rows.length}</div>} />);
    expect(screen.getByText("lines: 2")).toBeTruthy();
  });
});
