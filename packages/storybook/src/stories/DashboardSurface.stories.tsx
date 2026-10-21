import type { Meta, StoryObj } from "@storybook/react-vite";
import { composeAddons } from "@angee/app";
import {
  DashboardBars,
  DashboardSurface,
  MetricStrip,
  RowsListView,
  StatusIconGroup,
  type DashboardDefinition,
  type ListColumn,
  type StatusIconItem,
  type WidgetSpec,
} from "@angee/ui";
import { RuntimeRegistryFixture } from "./runtime-fixtures";

const meta = {
  title: "Views/DashboardSurface",
  component: DashboardSurface,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof DashboardSurface>;

export default meta;
type Story = StoryObj;

const KEY = "storybook.records";
const registry = composeAddons([], { canonicalModelLabel: (model) => model }).dashboards;

type ReviewRow = {
  id: string;
  title: string;
  reference: string;
  status: "clear" | "risk" | "conflict" | "unresolved";
  checks: readonly StatusIconItem[];
};

const CLEAR: readonly StatusIconItem[] = [
  { key: "identity", label: "Identity: matched", tone: "success" },
  { key: "duplicate", label: "Duplicate: none found", tone: "success" },
  { key: "contents", label: "Contents: matched", tone: "success" },
  { key: "reference", label: "Reference: matched", tone: "success" },
  { key: "source", label: "Source: matched", tone: "success" },
];

const ROWS: readonly ReviewRow[] = [
  { id: "1", title: "Northwind", reference: "REC-1042", status: "clear", checks: CLEAR },
  { id: "2", title: "Aster", reference: "REC-1041", status: "conflict", checks: CLEAR.map<StatusIconItem>((item) => item.key === "contents"
    ? { ...item, label: "Contents: values disagree", tone: "danger", description: "Compare the submitted value with the source document." } : item) },
  { id: "3", title: "Harbor", reference: "REC-1040", status: "risk", checks: CLEAR.map<StatusIconItem>((item) => item.key === "source"
    ? { ...item, label: "Source: review needed", tone: "warning", description: "A new sender submitted this record." } : item) },
  { id: "4", title: "Cedar", reference: "REC-1039", status: "clear", checks: CLEAR },
  { id: "5", title: "Willow", reference: "REC-1038", status: "unresolved", checks: CLEAR.map<StatusIconItem>((item) => item.key === "reference"
    ? { ...item, label: "Reference: not available", tone: "muted", description: "Waiting for the supporting document." } : item) },
  { id: "6", title: "Atlas", reference: "REC-1037", status: "clear", checks: CLEAR },
];

const COLUMNS: readonly ListColumn<ReviewRow>[] = [
  { field: "title", header: "Record", subline: "reference" },
  { field: "checks", header: "Checks", sortable: false, interactive: true,
    render: (row) => <StatusIconGroup label={`${row.title} checks`} items={row.checks} /> },
  { field: "status", header: "Assessment", widget: "statusBadge", options: [
    { value: "clear", label: "All checks passed" }, { value: "risk", label: "Risk" },
    { value: "conflict", label: "Conflict" }, { value: "unresolved", label: "Unresolved" },
  ], tone: { clear: "success", risk: "warning", conflict: "danger", unresolved: "muted" } },
];

function Inbox() {
  return <RowsListView rows={ROWS} columns={COLUMNS} scope="local" presentation="embedded" pageSize={3} />;
}

function Incoming() {
  return <DashboardBars title="Incoming records" orientation="vertical" series={[
    { key: "email", label: "Email" }, { key: "upload", label: "Upload" }, { key: "exchange", label: "Data exchange" },
  ]} points={[
    { key: "mon", label: "Mon", values: { email: 5, upload: 2, exchange: 1 } },
    { key: "tue", label: "Tue", values: { email: 3, upload: 1, exchange: 2 } },
    { key: "wed", label: "Wed", values: { email: 7, upload: 3, exchange: 2 } },
    { key: "thu", label: "Thu", values: { email: 4, upload: 2, exchange: 3 } },
    { key: "fri", label: "Fri", values: { email: 6, upload: 1, exchange: 2 } },
  ]} />;
}

function Attention() {
  return <DashboardBars title="Where attention is needed" series={[
    { key: "risk", label: "Risk", tone: "warning" }, { key: "conflict", label: "Conflict", tone: "danger" },
    { key: "unresolved", label: "Unresolved", tone: "muted" },
  ]} points={[
    { key: "identity", label: "Identity", values: {} },
    { key: "duplicate", label: "Duplicate", values: {} },
    { key: "contents", label: "Contents", values: { conflict: 1 } },
    { key: "reference", label: "Reference", values: { unresolved: 1 } },
    { key: "source", label: "Source", values: { risk: 1 } },
  ]} />;
}

function panel(id: string, title: string, x: number, y: number, w: number, h: number): WidgetSpec {
  return { schemaVersion: 1, kind: "authored", kindVersion: 1, id, title,
    data: { shape: "none", binding: { dashboardKey: KEY, widgetId: id } }, options: {},
    x, y, w, h, isArchived: false };
}

const definition: DashboardDefinition = {
  key: KEY, title: "Records overview", revision: "1", editable: false, columns: 12,
  widgets: [
    panel("incoming", "Incoming records · this week", 0, 0, 6, 5),
    panel("attention", "Where attention is needed", 6, 0, 6, 5),
    panel("inbox", "Inbox", 0, 5, 12, 6),
  ],
  authored: { incoming: Incoming, attention: Attention, inbox: Inbox },
};

/** The production dashboard, list, cards and charts composed together; fixtures contain no queries or policy engine. */
export const Overview: Story = {
  render: () => <RuntimeRegistryFixture runtime={{ dashboards: registry }}>
    <DashboardSurface target={{ scope: "addon", key: KEY }} definition={definition} className="h-screen"
      toolbar={<div className="px-4 pt-4">
        <MetricStrip density="prominent" metrics={[
          { label: "Open records", numericValue: 6, detail: "Awaiting processing" },
          { label: "All checks passed", numericValue: 3, tone: "success", detail: "No outstanding findings" },
          { label: "Risks", numericValue: 1, tone: "warning", detail: "Review the evidence" },
          { label: "Conflicts", numericValue: 1, tone: "danger", detail: "Sources disagree" },
          { label: "Unresolved", numericValue: 1, tone: "muted", detail: "Waiting for information" },
        ]} />
      </div>}
    />
  </RuntimeRegistryFixture>,
};
