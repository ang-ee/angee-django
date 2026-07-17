// @vitest-environment happy-dom

import {
  ModelMetadataProvider,
  type Row,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { defaultWidgets } from "../widgets";
import { deserializeFormSpec, type FormSpecFieldDescriptor } from "./form-spec";
import { LabeledDescriptorField } from "./MutationDialog";
import type { RowsValue } from "./RowsField";

Element.prototype.scrollIntoView = vi.fn();

const refineMocks = vi.hoisted(() => ({
  useList: vi.fn(),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return { ...actual, useList: refineMocks.useList };
});

const channelRows: Row[] = [
  { id: "chn-general", name: "General" },
];

const metadata: SchemaFieldMetadata = {
  types: {
    ChannelType: {
      typeName: "ChannelType",
      recordRepresentation: "name",
      fields: {
        id: { name: "id", kind: "scalar", scalar: "ID" },
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
      rootFields: {
        list: "channels",
        create: "insert_channels_one",
      },
      resource: {
        schemaName: "console",
        modelLabel: "Channel",
        appLabel: "",
        modelName: "Channel",
        publicIdField: "id",
        roots: {
          list: "channels",
          create: "insert_channels_one",
        },
        typeNames: { node: "ChannelType" },
        capabilities: ["list", "create"],
        fields: [],
        filterFields: [],
        orderFields: [],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

describe("rows widget", () => {
  afterEach(cleanup);

  beforeEach(() => {
    refineMocks.useList.mockReset();
    refineMocks.useList.mockImplementation(
      (options?: {
        resource?: string;
        queryOptions?: { enabled?: boolean };
      }) => {
        const enabled = options?.queryOptions?.enabled !== false;
        const rows = enabled && options?.resource === "channels"
          ? channelRows
          : [];
        return {
          result: { data: rows, total: rows.length },
          query: {
            isFetching: false,
            error: null,
            refetch: vi.fn(),
          },
        };
      },
    );
  });

  test("renders a deserialized array-of-objects schema through the real registry", async () => {
    const [field] = deserializeFormSpec(
      {
        type: "object",
        properties: {
          rows: {
            type: "array",
            label: "Mappings",
            items: {
              type: "object",
              properties: {
                source: { type: "string", label: "Source" },
                target: { type: "string", label: "Target" },
              },
            },
          },
        },
      },
      defaultWidgets,
    );
    if (!field) throw new Error("Expected a rows descriptor.");

    renderRows(
      <LabeledDescriptorField
        field={field}
        value={[{ source: "Archive A", target: "Channel A" }]}
        onChange={vi.fn()}
      />,
    );

    const table = await screen.findByRole("table", { name: "Mappings" });
    expect(table).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Source" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Target" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Source" })).toHaveProperty(
      "value",
      "Archive A",
    );
    expect(
      screen.getByText("Mappings").closest("label")?.getAttribute("for"),
    ).toBeNull();
  });

  test("propagates a cell edit as an immutable array update", async () => {
    const value: RowsValue = [
      { source: "Archive A", target: "Channel A" },
      { source: "Archive B", target: "Channel B" },
    ];
    const onChange = vi.fn();
    renderRows(
      <LabeledDescriptorField
        field={rowsField([
          { name: "source", label: "Source", widget: "text" },
          { name: "target", label: "Target", widget: "text" },
        ])}
        value={value}
        onChange={onChange}
      />,
    );

    const targets = await screen.findAllByRole("textbox", { name: "Target" });
    fireEvent.change(targets[1]!, {
      target: { value: "Channel C" },
    });

    const next = onChange.mock.calls[0]?.[0] as RowsValue;
    expect(next).toEqual([
      { source: "Archive A", target: "Channel A" },
      { source: "Archive B", target: "Channel C" },
    ]);
    expect(next).not.toBe(value);
    expect(next[0]).toBe(value[0]);
    expect(next[1]).not.toBe(value[1]);
    expect(value[1]?.target).toBe("Channel B");
  });

  test("loads a prefilled relation label before opening, then wires filters and inline create", async () => {
    const filters = [
      { field: "status", operator: "eq" as const, value: "active" },
    ];
    renderRows(
      <LabeledDescriptorField
        field={rowsField([
          {
            name: "target",
            label: "Target",
            widget: "many2one",
            relation: {
              resource: "Channel",
              labelField: "name",
              filters,
              create: { resource: "Channel" },
            },
          },
        ])}
        value={[{ target: "chn-general" }]}
        onChange={vi.fn()}
      />,
    );

    const trigger = await screen.findByRole("button", {
      name: "Target: General",
    });
    expect(screen.queryByPlaceholderText("Search…")).toBeNull();
    expect(refineMocks.useList).toHaveBeenCalledWith(
      expect.objectContaining({
        resource: "channels",
        filters,
        queryOptions: expect.objectContaining({ enabled: true }),
      }),
    );

    fireEvent.click(trigger);
    const search = await screen.findByPlaceholderText("Search…");
    fireEvent.change(search, { target: { value: "New target" } });
    await waitFor(() =>
      expect(screen.getByText("Create “New target”")).toBeTruthy(),
    );
  });

  test("binds dotted validation and required state to only the matching cell", async () => {
    renderRows(
      <LabeledDescriptorField
        field={rowsField([
          {
            name: "target",
            label: "Target",
            widget: "text",
            required: true,
          },
        ])}
        value={[{ target: "Channel A" }, { target: "" }]}
        messages={["rows.1.target: Choose a target."]}
        onChange={vi.fn()}
      />,
    );

    const inputs = await screen.findAllByRole("textbox", { name: "Target" });
    const message = screen.getByText("Choose a target.");
    expect(message.closest("td")).toBe(inputs[1]?.closest("td"));
    expect(inputs[0]?.getAttribute("aria-describedby")).toBeNull();
    expect(inputs[1]?.getAttribute("aria-describedby")?.split(" ")).toContain(
      message.id,
    );
    expect(inputs[1]?.getAttribute("aria-required")).toBe("true");
    expect(screen.getByText("Mappings").closest('[data-invalid=""]')).toBeNull();
    expect(message.closest('[data-invalid=""]')).not.toBeNull();
  });

  test("renders a compact read-only table", async () => {
    renderRows(
      <LabeledDescriptorField
        field={rowsField([
          { name: "source", label: "Source", widget: "text" },
          { name: "approved", label: "Approved", widget: "switch" },
        ])}
        value={[
          { source: "Archive A", approved: true },
          { source: "Archive B", approved: false },
        ]}
        readOnly
        onChange={vi.fn()}
      />,
    );

    expect(await screen.findByRole("table")).toBeTruthy();
    expect(
      screen.getByRole("columnheader", { name: "Approved" }).className,
    ).toContain("h-8");
    expect(screen.getByText("Archive A").closest("td")?.className).toContain(
      "px-2",
    );
    expect(screen.getByText("On").closest("td")).not.toBeNull();
    expect(screen.getByText("Off").closest("td")).not.toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});

function rowsField(
  rowTemplate: readonly FormSpecFieldDescriptor[],
): FormSpecFieldDescriptor {
  return {
    name: "rows",
    label: "Mappings",
    kind: "array",
    widget: "rows",
    rowTemplate,
  };
}

function renderRows(children: ReactElement): ReturnType<typeof render> {
  return render(
    <ModelMetadataProvider metadata={metadata}>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        {children}
      </AppRuntimeProvider>
    </ModelMetadataProvider>,
  );
}
