// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import type { DataResourceLinesMetadata } from "@angee/metadata";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { defaultWidgets } from "../widgets";
import { EditableLines } from "./EditableLines";

const LINES: DataResourceLinesMetadata = {
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
};

function Host({
  footer,
}: {
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
  return (
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <EditableLines control={form.control} name="lines" lines={LINES} footer={footer} />
    </AppRuntimeProvider>
  );
}

afterEach(cleanup);

describe("EditableLines", () => {
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
