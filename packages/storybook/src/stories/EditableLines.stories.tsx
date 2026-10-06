import { useWatch, useForm } from "react-hook-form";
import type { Meta, StoryObj } from "@storybook/react-vite";
import type { DataResourceLinesMetadata } from "@angee/metadata";
import {
  Button,
  EditableLines,
  Input,
  ModalsHost,
  defaultWidgets,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { RuntimeRegistryFixture } from "./runtime-fixtures";

const lines = {
  field: "lines",
  modelLabel: "story.Line",
  positionField: "position",
  fields: [
    {
      name: "label",
      kind: "scalar",
      scalar: "String",
      widget: "story.editableLinePatch",
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

const patchWidget = {
  read: ({ value }: WidgetRenderProps) => <span>{String(value ?? "")}</span>,
  edit: ({ value, onChange, onRowChange, field }: WidgetRenderProps) => (
    <div className="grid gap-1.5">
      <Input
        {...field?.controlProps}
        aria-label="Line label"
        value={String(value ?? "")}
        onChange={(event) => onChange?.(event.target.value)}
      />
      <div className="flex flex-wrap gap-1">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => onRowChange?.({ quantity: 8 })}
        >
          Patch quantity now
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => window.setTimeout(() => onRowChange?.({ label: "Async resolved" }), 800)}
        >
          Patch label in 800ms
        </Button>
      </div>
    </div>
  ),
} satisfies WidgetDefinition;

const meta = {
  title: "Views/EditableLines",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const WidgetRowPatches: Story = {
  render: () => <EditableLinesDemo />,
};

/** A system row the backend locks keeps its place; only its unlocked cells edit. */
export const LockedSystemRows: Story = {
  render: () => <LockedLinesDemo />,
};

function LockedLinesDemo() {
  const form = useForm<Record<string, unknown>>({
    defaultValues: {
      lines: [
        { id: "line-system", label: "Intake", quantity: 1, position: 0, locked_fields: ["label"] },
        { id: "line-one", label: "Widget", quantity: 2, position: 1, locked_fields: [] },
        { id: "line-two", label: "Gadget", quantity: 5, position: 2, locked_fields: [] },
      ],
    },
  });
  return (
    <RuntimeRegistryFixture runtime={{ widgets: defaultWidgets }}>
      <ModalsHost>
        <div className="max-w-5xl">
          <EditableLines
            control={form.control}
            setValue={form.setValue}
            name="lines"
            lines={{
              ...lines,
              lockField: "locked_fields",
              // Plain cells: the system row's label reads, its quantity still edits.
              fields: lines.fields.map((field) => ({ ...field, widget: null })),
            }}
          />
        </div>
      </ModalsHost>
    </RuntimeRegistryFixture>
  );
}

function EditableLinesDemo() {
  const form = useForm<Record<string, unknown>>({
    defaultValues: {
      lines: [
        { id: "line-one", label: "Widget", quantity: 2, position: 0 },
        { id: "line-two", label: "Gadget", quantity: 5, position: 1 },
      ],
    },
  });
  const liveLines = useWatch({ control: form.control, name: "lines" });
  return (
    <RuntimeRegistryFixture
      runtime={{ widgets: { ...defaultWidgets, "story.editableLinePatch": patchWidget } }}
    >
      <ModalsHost>
        <div className="grid max-w-5xl gap-4">
          <p className="text-sm text-fg-muted">
            Choose Quantity in the header's Visible fields menu, then schedule a delayed label patch and edit the quantity while it is pending.
          </p>
          <EditableLines
            control={form.control}
            setValue={form.setValue}
            name="lines"
            lines={lines}
            primaryFields={["label"]}
          />
          <pre aria-label="Live line values" className="overflow-auto rounded-8 bg-inset p-3 text-xs text-fg">
            {JSON.stringify(liveLines, null, 2)}
          </pre>
        </div>
      </ModalsHost>
    </RuntimeRegistryFixture>
  );
}
