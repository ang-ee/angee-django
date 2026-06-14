import type { Meta, StoryObj } from "@storybook/react-vite";
import { MetricGrid } from "@angee/base";

const meta = {
  title: "Fragments/MetricGrid",
  component: MetricGrid,
  parameters: { layout: "padded" },
} satisfies Meta<typeof MetricGrid>;

export default meta;

type Story = StoryObj;

export const Grid: Story = {
  render: () => (
    <MetricGrid
      className="max-w-5xl"
      metrics={[
        { label: "Published", value: "184", icon: "circle-check", tone: "success" },
        { label: "Drafts", value: "27", icon: "file", tone: "warning" },
        { label: "Alerts", value: "5", icon: "triangle-alert", tone: "danger" },
        { label: "Comments", value: "62", icon: "comments", tone: "info" },
      ]}
    />
  ),
};
