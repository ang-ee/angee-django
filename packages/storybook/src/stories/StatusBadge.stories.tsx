import type { Meta, StoryObj } from "@storybook/react-vite";
import { StatusBadge } from "@angee/ui";

const OPTIONS = [
  { value: "RUNNING", label: "Running" },
  { value: "PAUSED", label: "Paused" },
  { value: "FAILED", label: "Failed" },
];

const meta = {
  title: "Fragments/StatusBadge",
  component: StatusBadge,
  args: { value: "RUNNING", options: OPTIONS },
  parameters: { layout: "padded" },
} satisfies Meta<typeof StatusBadge>;

export default meta;

type Story = StoryObj<typeof meta>;

/** The one status pill: the value's tone from the composed vocabulary, its label from the options. */
export const Default: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      {OPTIONS.map((option) => <StatusBadge key={option.value} value={option.value} options={OPTIONS} />)}
      <StatusBadge value="unknown_member" options={OPTIONS} />
    </div>
  ),
};

export const Displays: Story = {
  render: () => (
    <div className="grid gap-2">
      <StatusBadge value="RUNNING" options={OPTIONS} display="pill" />
      <StatusBadge value="RUNNING" options={OPTIONS} display="dot" />
      <StatusBadge value="RUNNING" options={OPTIONS} display="text" />
    </div>
  ),
};

/** A hint behind the pill reads as its native tooltip. */
export const WithHint: Story = {
  render: () => <StatusBadge value="protected" title="A required or generated secret cannot be deleted.">Required</StatusBadge>,
};
