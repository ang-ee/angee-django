import { useState, type ReactElement, type ReactNode } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { defaultWidgets, type WidgetDefinition, type WidgetField } from "@angee/ui";

import { RuntimeRegistryFixture } from "./runtime-fixtures";

const selectionOptions = [
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
];

const statusOptions = [
  { value: "DRAFT", label: "Draft" },
  { value: "IN_REVIEW", label: "In review" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
];

const stageOptions = [
  { value: "new", label: "New" },
  { value: "qualified", label: "Qualified" },
  { value: "proposal", label: "Proposal" },
  { value: "won", label: "Won" },
];

const runtimeOptions = [
  { value: "STOPPED", label: "Stopped" },
  { value: "RUNNING", label: "Running" },
  { value: "ERROR", label: "Error" },
  { value: "WARNING", label: "Warning" },
];

const ownerOptions = [
  { value: "ada", label: "Ada Lovelace" },
  { value: "grace", label: "Grace Hopper" },
  { value: "katherine", label: "Katherine Johnson" },
];

const recordOptions = [
  { value: "rec-1001", label: "Acme onboarding" },
  { value: "rec-1002", label: "Globex renewal" },
  { value: "rec-1003", label: "Initech rollout" },
];

type ThemeValue = "light" | "dark" | "system";

type OwnerCellStoryValue =
  | string
  | {
      id?: string;
      value?: string;
      label?: ReactNode;
      name?: string;
      avatarUrl?: string;
      src?: string;
    }
  | null;

type JsonWidgetValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonWidgetValue[]
  | { readonly [key: string]: JsonWidgetValue };

type NumericWidgetValue = number | string | null;

const meta = {
  title: "Widgets/Scalar Fields",
  parameters: {
    layout: "padded",
  },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const EditAndRead: Story = {
  render: () => (
    <RuntimeRegistryFixture>
      <div className="max-w-6xl overflow-x-auto">
        <div className="min-w-[960px] rounded-6 border border-border bg-sheet">
          <div className="grid grid-cols-[10rem_minmax(14rem,1fr)_minmax(14rem,1fr)_minmax(14rem,1fr)] gap-3 border-b border-border-subtle px-4 py-2 text-2xs font-semibold uppercase text-fg-muted">
            <span>Kind</span>
            <span>Edit</span>
            <span>Read</span>
            <span>Cell</span>
          </div>
          <div className="divide-y divide-border-subtle">
            <WidgetExample<NumericWidgetValue>
              kind="integer"
              widget={defaultWidgets.integer}
              initialValue={42}
              field={{ label: "Quantity" }}
            />
            <WidgetExample<NumericWidgetValue>
              kind="float"
              widget={defaultWidgets.float}
              initialValue={19.75}
              field={{ label: "Rate" }}
            />
            <WidgetExample
              kind="email"
              widget={defaultWidgets.email}
              initialValue="ada@example.com"
              field={{ label: "Email" }}
            />
            <WidgetExample
              kind="url"
              widget={defaultWidgets.url}
              initialValue="https://angee.dev/docs"
              field={{ label: "Website" }}
            />
            <WidgetExample
              kind="phone"
              widget={defaultWidgets.phone}
              initialValue="+1 555 010 1842"
              field={{ label: "Phone" }}
            />
            <WidgetExample
              kind="boolean"
              widget={defaultWidgets.boolean}
              initialValue={true}
              field={{ label: "Approved" }}
            />
            <WidgetExample
              kind="booleanToggle"
              widget={defaultWidgets.booleanToggle}
              initialValue={false}
              field={{ label: "Published" }}
            />
            <WidgetExample<string | Date | null>
              kind="date"
              widget={defaultWidgets.date}
              initialValue="2026-06-16"
              field={{ label: "Due date" }}
            />
            <WidgetExample<JsonWidgetValue>
              kind="json"
              widget={defaultWidgets.json}
              initialValue={{
                flags: ["import", "sync"],
                owner: "Ada Lovelace",
                retries: 2,
              }}
              field={{ label: "Metadata" }}
            />
            <WidgetExample
              kind="selection"
              widget={defaultWidgets.selection}
              initialValue="active"
              field={{ label: "Status", options: selectionOptions }}
            />
            <WidgetExample
              kind="combobox"
              widget={defaultWidgets.combobox}
              initialValue="IN_REVIEW"
              field={{ label: "Search status", options: statusOptions }}
            />
            <WidgetExample
              kind="statusBadge"
              widget={defaultWidgets.statusBadge}
              initialValue="IN_REVIEW"
              field={{ label: "Status", options: statusOptions }}
            />
            <WidgetExample
              kind="colorDot"
              widget={defaultWidgets.colorDot}
              initialValue="RUNNING"
              field={{ label: "Runtime", options: runtimeOptions }}
            />
            <WidgetExample<number | null>
              kind="progressBar"
              widget={defaultWidgets.progressBar}
              initialValue={64}
              field={{ label: "Completion" }}
            />
            <WidgetExample
              kind="ribbon"
              widget={defaultWidgets.ribbon}
              initialValue="proposal"
              field={{ label: "Stage", options: stageOptions }}
            />
            <WidgetExample<OwnerCellStoryValue>
              kind="ownerCell"
              widget={defaultWidgets.ownerCell}
              initialValue={{ id: "ada", name: "Ada Lovelace" }}
              field={{ label: "Owner", options: ownerOptions }}
            />
            <WidgetExample<ThemeValue>
              kind="themePicker"
              widget={defaultWidgets.themePicker}
              initialValue="system"
              field={{ label: "Theme" }}
            />
            <WidgetExample<unknown>
              kind="many2one"
              widget={defaultWidgets.many2one}
              initialValue="rec-1002"
              field={{ label: "Account", options: recordOptions }}
            />
            <WidgetExample<readonly unknown[]>
              kind="many2many"
              widget={defaultWidgets.many2many}
              initialValue={["rec-1001", "rec-1003"]}
              field={{ label: "Linked records", options: recordOptions }}
            />
          </div>
        </div>
      </div>
    </RuntimeRegistryFixture>
  ),
};

function WidgetExample<TValue>({
  kind,
  widget,
  initialValue,
  field,
}: {
  kind: string;
  widget: WidgetDefinition<TValue>;
  initialValue: TValue;
  field: WidgetField;
}): ReactElement {
  const [value, setValue] = useState<TValue>(initialValue);
  const Edit = widget.edit ?? widget.read;
  const Read = widget.read;
  const Cell = widget.cell ?? widget.read;
  const widgetField = { name: kind, ...field };

  return (
    <div className="grid grid-cols-[10rem_minmax(14rem,1fr)_minmax(14rem,1fr)_minmax(14rem,1fr)] items-start gap-3 px-4 py-3">
      <span className="pt-1.5 font-mono text-xs text-fg-muted">{kind}</span>
      <div className="min-w-0">
        <Edit value={value} field={widgetField} onChange={setValue} />
      </div>
      <div className="min-w-0 pt-1.5">
        <Read value={value} field={widgetField} readOnly />
      </div>
      <div className="min-w-0 pt-1.5">
        <Cell value={value} field={widgetField} readOnly />
      </div>
    </div>
  );
}
