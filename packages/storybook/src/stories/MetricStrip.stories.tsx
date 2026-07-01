import type { Meta, StoryObj } from "@storybook/react-vite";
import { MetricStrip } from "@angee/ui";

const meta = {
  title: "Fragments/MetricStrip",
  component: MetricStrip,
  parameters: { layout: "padded" },
} satisfies Meta<typeof MetricStrip>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Strip: Story = {
  render: () => (
    <MetricStrip
      className="max-w-5xl"
      metrics={[
        { label: "Services", value: "18", icon: "activity", detail: "15 healthy" },
        { label: "Workspaces", value: "7", icon: "layout-dashboard", detail: "2 active" },
        { label: "Sources", value: "12", icon: "archive" },
        { label: "Jobs", value: "31", icon: "agent", detail: "4 running" },
      ]}
    />
  ),
};
