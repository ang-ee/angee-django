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
}: {
  inspectContext?: boolean;
  footer?: (rows: readonly Record<string, unknown>[]) => React.ReactNode;
}): React.ReactElement {
  const form = useForm<Record<string, unknown>>({
    defaultValues: {
      lines: [
        { label: "Widget", quantity: 2, position: 0 },
        { label: "Gadget", quantity: 5, position: 1 },
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
        name="lines"
        lines={lines}
        parentRow={{ company: "Acme" }}
        footer={footer}
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
        <EditableLines control={form.control} name="lines" lines={lines} readOnly={readOnly} />
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
