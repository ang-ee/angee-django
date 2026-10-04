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
