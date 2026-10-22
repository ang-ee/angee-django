import type { Meta, StoryObj } from "@storybook/react-vite";
import { Alert, ReportPage, RowsListView, type ListColumn } from "@angee/ui";

const meta = {
  title: "Page/ReportPage",
  component: ReportPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ReportPage>;

export default meta;

type Story = StoryObj;

type BalanceRow = { id: string; code: string; name: string; debit: string; credit: string; currency_code: string };

const ROWS: readonly BalanceRow[] = [
  { id: "1000", code: "1000", name: "Cash", debit: "12400.00", credit: "0.00", currency_code: "EUR" },
  { id: "2000", code: "2000", name: "Accounts payable", debit: "0.00", credit: "8120.50", currency_code: "EUR" },
  { id: "4000", code: "4000", name: "Sales", debit: "0.00", credit: "4279.50", currency_code: "EUR" },
];

const COLUMNS: readonly ListColumn<BalanceRow>[] = [
  { field: "code", header: "Code" },
  { field: "name", header: "Account" },
  { field: "debit", header: "Debit", widget: "money", currencyField: "currency_code", align: "right", sortable: false },
  { field: "credit", header: "Credit", widget: "money", currencyField: "currency_code", align: "right", sortable: false },
];

const PARAMETERS = {
  id: "parameters",
  label: "Report parameters",
  args: [
    { name: "dateFrom", label: "From", widget: "date", defaultValue: "2026-01-01" },
    { name: "dateTo", label: "To", widget: "date", defaultValue: "2026-10-20" },
  ],
  submit: () => ({ ok: true, message: "" }),
} as const;

export const ConfiguredRun: Story = {
  render: () => (
    <ReportPage
      title="Trial balance"
      crumbs="Accounting · Reports"
      facts={["Acme GmbH", "EUR", "Jan 1 – Oct 20, 2026"]}
      description="Posted balances and movements only. Totals cover the complete report."
      status={{ label: "Balanced", tone: "success" }}
      views={[
        { id: "summary", label: "Summary", href: "#", active: true },
        { id: "details", label: "Details", href: "#" },
      ]}
      parameters={PARAMETERS}
      actions={[
        { id: "refresh", label: "Refresh", icon: "refresh-cw", run: () => undefined },
        { id: "export", label: "Export CSV", icon: "download", run: () => undefined },
      ]}
      metrics={[
        { label: "Debit", numericValue: 12400, format: { style: "currency", currency: "EUR" } },
        { label: "Credit", numericValue: 12400, format: { style: "currency", currency: "EUR" } },
      ]}
    >
      <RowsListView rows={ROWS} columns={COLUMNS} scope="local" />
    </ReportPage>
  ),
};

export const UnbalancedRun: Story = {
  render: () => (
    <ReportPage
      title="Trial balance"
      facts={["Acme GmbH", "EUR", "Jan 1 – Oct 20, 2026"]}
      status={{ label: "Out of balance", tone: "danger" }}
      notice={<Alert tone="warning" title="Debits and credits do not balance. Review the posted ledger." />}
      parameters={PARAMETERS}
      actions={[{ id: "refresh", label: "Refresh", run: () => undefined }]}
    >
      <RowsListView rows={ROWS} columns={COLUMNS} scope="local" />
    </ReportPage>
  ),
};

export const Unconfigured: Story = {
  render: () => (
    <ReportPage
      title="Aged receivables"
      configured={false}
      empty={{ title: "Choose a company, cutoff date and balance type." }}
      parameters={PARAMETERS}
    />
  ),
};
