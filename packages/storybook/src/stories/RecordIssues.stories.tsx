import type { Meta, StoryObj } from "@storybook/react-vite";
import { useRef } from "react";
import { Input, RecordIssues } from "@angee/ui";

const meta = {
  title: "Fragments/RecordIssues",
  component: RecordIssues,
  parameters: { layout: "padded" },
  args: {
    items: [
      { id: "details", tone: "info", message: "Review the optional details." },
      { id: "ready", tone: "success", message: "The record is ready." },
      { id: "title", tone: "warning", message: "A clearer title would help." },
      { id: "owner", tone: "danger", message: "Choose an owner before continuing." },
    ],
  },
} satisfies Meta<typeof RecordIssues>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Tones: Story = {};
export const Empty: Story = { args: { items: [] } };

function FocusExample() {
  const input = useRef<HTMLInputElement>(null);
  return <div className="max-w-xl space-y-4">
    <RecordIssues
      items={[{ id: "title", tone: "warning", message: "Enter a title", field: "title" }]}
      onFocusField={() => input.current?.focus()}
    />
    <Input ref={input} aria-label="Title" placeholder="Title" />
  </div>;
}

export const FieldFocus: Story = { render: () => <FocusExample /> };

const FIELD_LABELS: Record<string, string> = { variables: "Variables", sample: "Sample values" };

/** Two issues with the same words under two fields: each is titled by its field's label. */
export const FieldLabels: Story = {
  args: {
    items: [
      { id: "variables", tone: "danger", message: "Give an object.", field: "variables" },
      { id: "sample", tone: "danger", message: "Give an object.", field: "sample" },
    ],
    onFocusField: () => undefined,
    fieldLabel: (field: string) => FIELD_LABELS[field],
  },
};

/** Issues about edits not yet submitted: one polite region, in the page with no issues too; none is an alert. */
export const Polite: Story = { args: { polite: true } };
