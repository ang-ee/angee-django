import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
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

export const Prominent: Story = {
  render: () => (
    <MetricStrip
      density="prominent"
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

export const Loading: Story = {
  render: () => <MetricStrip loading loadingMessage="Loading metrics…" className="max-w-5xl" />,
};

export const SelectableSummary: Story = { render: () => <Summary /> };

function Summary() {
  const [selected, setSelected] = useState("all");
  return <MetricStrip density="prominent" className="w-full max-w-7xl" metrics={([
    { id: "all", label: "Open records", numericValue: 26, detail: "Received this month", tone: "neutral" },
    { id: "clear", label: "All checks passed", numericValue: 16, detail: "No outstanding findings", tone: "success" },
    { id: "risk", label: "Risks", numericValue: 5, detail: "Review the evidence", tone: "warning" },
    { id: "conflict", label: "Conflicts", numericValue: 3, detail: "Sources disagree", tone: "danger" },
    { id: "unknown", label: "Unresolved", numericValue: 2, detail: "Waiting for information", tone: "muted" },
  ] as const).map((metric) => ({
    ...metric,
    selected: selected === metric.id,
    onClick: () => setSelected(metric.id),
  }))} />;
}
