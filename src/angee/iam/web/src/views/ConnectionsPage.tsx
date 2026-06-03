import { useMemo, type ReactElement, type ReactNode } from "react";

import {
  Alert,
  Badge,
  Code,
  RowsListView,
  Spinner,
  type BadgeVariant,
  type ListColumn,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  IAM_CONNECTION_SUMMARY_QUERY,
  IAM_OAUTH_CLIENTS_QUERY,
  type IAMConnectionSummaryData,
  type IAMConnectionSummaryVariables,
  type IAMCredentialSummary,
  type IAMExternalAccountSummary,
  type IAMOAuthClient,
  type IAMOAuthClientsData,
  type IAMOAuthClientsVariables,
  type IAMVendorSummary,
} from "../documents";

const SUMMARY_LIMIT = 8;
const OAUTH_CLIENT_LIMIT = 500;

const oauthClientColumns: readonly ListColumn<IAMOAuthClient>[] = [
  {
    field: "displayName",
    header: "Client",
    render: (row) => (
      <span className="font-medium text-fg">{row.displayName}</span>
    ),
  },
  { field: "vendorLabel", header: "Vendor" },
  {
    field: "environment",
    header: "Environment",
    render: (row) => <Code truncate>{row.environment}</Code>,
  },
  {
    field: "isEnabled",
    header: "Enabled",
    render: (row) => (
      <Badge variant={row.isEnabled ? "success" : "default"}>
        {row.isEnabled ? "Enabled" : "Disabled"}
      </Badge>
    ),
  },
  {
    field: "configurationState",
    header: "Configuration",
    render: (row) => (
      <Badge variant={statusVariant(row.configurationState)}>
        {row.configurationState}
      </Badge>
    ),
  },
  {
    field: "supportsPkce",
    header: "PKCE",
    render: (row) => (
      <Badge variant={row.supportsPkce ? "info" : "default"}>
        {row.supportsPkce ? "Supported" : "Not supported"}
      </Badge>
    ),
  },
];

export function ConnectionsPage(): ReactElement {
  const variables = useMemo<IAMOAuthClientsVariables>(
    () => ({ pagination: { offset: 0, limit: OAUTH_CLIENT_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery<
    IAMOAuthClientsData,
    IAMOAuthClientsVariables
  >(IAM_OAUTH_CLIENTS_QUERY, variables);
  const rows = useMemo(
    () => [...(query.data?.oauthClients.results ?? [])],
    [query.data],
  );

  return (
    <div className="flex flex-col gap-4">
      <ConnectionSummary />
      <RowsListView
        rows={rows}
        columns={oauthClientColumns}
        fetching={query.fetching}
        error={query.error}
        pageSize={50}
      />
    </div>
  );
}

function ConnectionSummary(): ReactElement {
  const query = useAuthoredQuery<
    IAMConnectionSummaryData,
    IAMConnectionSummaryVariables
  >(IAM_CONNECTION_SUMMARY_QUERY, {
    pagination: { offset: 0, limit: SUMMARY_LIMIT },
  });

  if (query.error) {
    return (
      <Alert intent="danger" title="Connection summary unavailable">
        {query.error.message}
      </Alert>
    );
  }

  if (query.fetching && !query.data) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-sheet px-4 py-3 text-13 text-fg-muted">
        <Spinner size="sm" />
        Loading connection summary...
      </div>
    );
  }

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <SummarySection
        title="Vendors"
        total={query.data?.vendors.totalCount ?? 0}
        items={(query.data?.vendors.results ?? []).map((vendor) => (
          <VendorSummaryRow key={vendor.id} vendor={vendor} />
        ))}
      />
      <SummarySection
        title="External Accounts"
        total={query.data?.externalAccounts.totalCount ?? 0}
        items={(query.data?.externalAccounts.results ?? []).map((account) => (
          <ExternalAccountSummaryRow key={account.id} account={account} />
        ))}
      />
      <SummarySection
        title="Credential Health"
        total={query.data?.credentialHealth.totalCount ?? 0}
        items={(query.data?.credentialHealth.results ?? []).map((credential) => (
          <CredentialSummaryRow key={credential.id} credential={credential} />
        ))}
      />
    </div>
  );
}

function SummarySection({
  title,
  total,
  items,
}: {
  title: string;
  total: number;
  items: readonly ReactNode[];
}): ReactElement {
  return (
    <section className="min-w-0 rounded-md border border-border-subtle bg-sheet">
      <div className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <h2 className="m-0 truncate text-sm font-semibold text-fg">{title}</h2>
        <Badge>{total.toLocaleString()}</Badge>
      </div>
      <div className="divide-y divide-border-subtle">
        {items.length > 0 ? (
          items
        ) : (
          <p className="m-0 px-4 py-4 text-13 text-fg-muted">No records.</p>
        )}
      </div>
    </section>
  );
}

function VendorSummaryRow({
  vendor,
}: {
  vendor: IAMVendorSummary;
}): ReactElement {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 px-4 py-3">
      <span className="truncate text-13 font-medium text-fg">
        {vendor.displayName || vendor.slug}
      </span>
      <Code truncate variant="muted">
        {vendor.slug}
      </Code>
    </div>
  );
}

function ExternalAccountSummaryRow({
  account,
}: {
  account: IAMExternalAccountSummary;
}): ReactElement {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-13 font-medium text-fg">
          {account.displayName || account.email}
        </div>
        <div className="truncate text-2xs text-fg-muted">
          {account.vendor.displayName}
        </div>
      </div>
      <Badge variant={statusVariant(account.credentialStatus)}>
        {account.credentialStatus}
      </Badge>
    </div>
  );
}

function CredentialSummaryRow({
  credential,
}: {
  credential: IAMCredentialSummary;
}): ReactElement {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-13 font-medium text-fg">
          {credential.oauthClient.displayName}
        </div>
        <div className="truncate text-2xs text-fg-muted">
          {credential.externalAccount?.email ?? credential.kind}
        </div>
      </div>
      <Badge variant={statusVariant(credential.status)}>
        {credential.status}
      </Badge>
    </div>
  );
}

function statusVariant(status: string): BadgeVariant {
  const normalized = status.toUpperCase();
  if (["ACTIVE", "READY", "OK", "VALID", "ENABLED"].includes(normalized)) {
    return "success";
  }
  if (["WARNING", "PENDING", "STALE"].includes(normalized)) return "warning";
  if (["ERROR", "FAILED", "EXPIRED", "REVOKED", "DISABLED"].includes(normalized)) {
    return "danger";
  }
  return "default";
}
