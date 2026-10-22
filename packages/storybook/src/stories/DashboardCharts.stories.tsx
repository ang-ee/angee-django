import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  DashboardBars,
  DashboardDonut,
  type DashboardBarSelection,
  type DashboardSeriesDefinition,
  type DashboardStackedPoint,
} from "@angee/ui";

const meta = {
  title: "Views/Dashboard charts",
  component: DashboardBars,
  parameters: { layout: "padded" },
} satisfies Meta<typeof DashboardBars>;

export default meta;
type Story = StoryObj;

const SOURCES: readonly DashboardSeriesDefinition[] = [
  { key: "email", label: "Email" },
  { key: "upload", label: "Upload" },
  { key: "exchange", label: "Data exchange" },
];
const DAYS: readonly DashboardStackedPoint[] = [
  { key: "2026-10-15", label: "Thu", values: { email: 5, upload: 2, exchange: 1 } },
  { key: "2026-10-16", label: "Fri", values: { email: 3, upload: 1, exchange: 2 } },
  { key: "2026-10-17", label: "Sat", values: { email: 1, exchange: 1 } },
  { key: "2026-10-18", label: "Sun", values: {} },
  { key: "2026-10-19", label: "Mon", values: { email: 7, upload: 3, exchange: 2 } },
  { key: "2026-10-20", label: "Tue", values: { email: 4, upload: 2, exchange: 3 } },
  { key: "2026-10-21", label: "Wed", values: { email: 6, upload: 1, exchange: 2 } },
];
const OUTCOMES: readonly DashboardSeriesDefinition[] = [
  { key: "risk", label: "Risk", tone: "warning" },
  { key: "conflict", label: "Conflict", tone: "danger" },
  { key: "unresolved", label: "Unresolved", tone: "muted" },
];
const CHECKS: readonly DashboardStackedPoint[] = [
  { key: "identity", label: "Identity", values: { risk: 2, unresolved: 1 } },
  { key: "duplicate", label: "Duplicate", values: { conflict: 1 } },
  { key: "contents", label: "Contents", values: { risk: 3, conflict: 2 } },
  { key: "reference", label: "Reference", values: { unresolved: 2 } },
  { key: "source", label: "Source", values: { risk: 1, conflict: 1 } },
];

export const Default: Story = { name: "Stacked timeline", render: () => <Timeline /> };

function Timeline() {
  const [selection, setSelection] = useState<DashboardBarSelection>();
  return <div className="max-w-3xl space-y-4">
    <div className="h-72">
      <DashboardBars title="Incoming records" orientation="vertical" series={SOURCES} points={DAYS} onSelect={setSelection} />
    </div>
    <p role="status" className="text-13 text-fg-muted">
      {selection ? `${selection.key} · ${SOURCES.find((item) => item.key === selection.seriesKey)?.label}` : "Select a segment to inspect its records."}
    </p>
  </div>;
}

export const AttentionBreakdown: Story = {
  render: () => <div className="h-72 max-w-3xl">
    <DashboardBars title="Where attention is needed" series={OUTCOMES} points={CHECKS} />
  </div>,
};

export const SignedValues: Story = {
  render: () => <div className="h-56 max-w-3xl">
    <DashboardBars title="Balance adjustments" points={[
      { key: "increase", label: "Increase", value: 80, tone: "info" },
      { key: "decrease", label: "Decrease", value: -40, tone: "warning" },
      { key: "unchanged", label: "Unchanged", value: 0, tone: "muted" },
    ]} />
  </div>,
};

export const Composition: Story = {
  parameters: { owner: DashboardDonut },
  render: () => <div className="h-56 max-w-xl">
    <DashboardDonut title="Review status" totalLabel="records" points={[
      { key: "clear", label: "All checks passed", value: 16, tone: "success" },
      { key: "risk", label: "Risks", value: 5, tone: "warning" },
      { key: "conflict", label: "Conflicts", value: 3, tone: "danger" },
      { key: "unresolved", label: "Unresolved", value: 2, tone: "muted" },
    ]} />
  </div>,
};

export const Empty: Story = { render: () => <DashboardBars title="No incoming records" points={[]} /> };
