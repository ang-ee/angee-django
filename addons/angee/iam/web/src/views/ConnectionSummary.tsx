import {
  useEffect,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  Alert,
  Badge,
  Code,
  Spinner,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  IAM_CONNECTION_SUMMARY_QUERY,
  type IAMConnectionSummaryData,
  type IAMConnectionSummaryVariables,
  type IAMCredentialSummary,
  type IAMExternalAccountSummary,
  type IAMVendorSummary,
} from "../documents";
import { toneFor } from "./ConnectionListColumns";

const SUMMARY_LIMIT = 8;

export function ConnectionSummary({
  refreshVersion,
  onAccountEdit,
  onVendorEdit,
}: {
  refreshVersion: number;
  onAccountEdit: (account: IAMExternalAccountSummary) => void;
  onVendorEdit: (vendor: IAMVendorSummary) => void;
}): ReactElement {
  const query = useAuthoredQuery<
    IAMConnectionSummaryData,
    IAMConnectionSummaryVariables
  >(IAM_CONNECTION_SUMMARY_QUERY, {
    pagination: { offset: 0, limit: SUMMARY_LIMIT },
  });
  const handledRefreshRef = useRef(refreshVersion);

  useEffect(() => {
    if (handledRefreshRef.current === refreshVersion) return;
    handledRefreshRef.current = refreshVersion;
    if (refreshVersion > 0) query.refetch();
  }, [query, refreshVersion]);

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
          <VendorSummaryRow
            key={vendor.id}
            vendor={vendor}
            onClick={() => onVendorEdit(vendor)}
          />
        ))}
      />
      <SummarySection
        title="External Accounts"
        total={query.data?.externalAccounts.totalCount ?? 0}
        items={(query.data?.externalAccounts.results ?? []).map((account) => (
          <ExternalAccountSummaryRow
            key={account.id}
            account={account}
            onClick={() => onAccountEdit(account)}
          />
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
    <section className="min-w-0 bg-sheet">
      <div className="flex items-center justify-between gap-3 border-b border-border-subtle py-2">
        <h2 className="m-0 truncate text-sm font-semibold text-fg">{title}</h2>
        <Badge>{total.toLocaleString()}</Badge>
      </div>
      <div className="divide-y divide-border-subtle">
        {items.length > 0 ? items : <p className="m-0 py-3 text-13 text-fg-muted">No records.</p>}
      </div>
    </section>
  );
}

function VendorSummaryRow({
  vendor,
  onClick,
}: {
  vendor: IAMVendorSummary;
  onClick: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className="flex w-full min-w-0 items-center justify-between gap-3 py-3 text-left outline-none hover:bg-inset focus-visible:focus-ring"
      onClick={onClick}
    >
      <span className="truncate text-13 font-medium text-fg">
        {vendor.displayName || vendor.slug}
      </span>
      <Code truncate variant="muted">
        {vendor.slug}
      </Code>
    </button>
  );
}

function ExternalAccountSummaryRow({
  account,
  onClick,
}: {
  account: IAMExternalAccountSummary;
  onClick: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className="flex w-full min-w-0 items-center justify-between gap-3 py-3 text-left outline-none hover:bg-inset focus-visible:focus-ring"
      onClick={onClick}
    >
      <span className="min-w-0">
        <span className="block truncate text-13 font-medium text-fg">
          {account.displayName || account.email || account.externalId}
        </span>
        <span className="block truncate text-2xs text-fg-muted">
          {account.vendor.displayName}
        </span>
      </span>
      <Badge variant={toneFor(account.credentialStatus)}>
        {account.credentialStatus || "None"}
      </Badge>
    </button>
  );
}

function CredentialSummaryRow({
  credential,
}: {
  credential: IAMCredentialSummary;
}): ReactElement {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <div className="truncate text-13 font-medium text-fg">
          {credential.oauthClient.displayName}
        </div>
        <div className="truncate text-2xs text-fg-muted">
          {credential.externalAccount?.email ?? credential.kind}
        </div>
      </div>
      <Badge variant={toneFor(credential.status)}>{credential.status}</Badge>
    </div>
  );
}
