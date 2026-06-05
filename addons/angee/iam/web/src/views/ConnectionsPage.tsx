import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Code,
  Dialog,
  Glyph,
  Input,
  RowsListView,
  Select,
  Spinner,
  Textarea,
  useToast,
  type BadgeVariant,
  type ListColumn,
} from "@angee/base";
import {
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/sdk";

import {
  IAM_CONNECTION_SUMMARY_QUERY,
  IAM_CREATE_EXTERNAL_ACCOUNT_MUTATION,
  IAM_CREATE_OAUTH_CLIENT_MUTATION,
  IAM_CREATE_VENDOR_MUTATION,
  IAM_EXTERNAL_ACCOUNTS_QUERY,
  IAM_OAUTH_CLIENTS_QUERY,
  IAM_UPDATE_OAUTH_CLIENT_MUTATION,
  IAM_UPDATE_VENDOR_MUTATION,
  IAM_USERS_QUERY,
  IAM_VENDOR_OPTIONS_QUERY,
  type IAMConnectionSummaryData,
  type IAMConnectionSummaryVariables,
  type IAMCreateExternalAccountData,
  type IAMCreateOAuthClientData,
  type IAMCreateVendorData,
  type IAMExternalAccountInputVariables,
  type IAMExternalAccountSummary,
  type IAMExternalAccountsData,
  type IAMExternalAccountsVariables,
  type IAMOAuthClient,
  type IAMOAuthClientInputVariables,
  type IAMOAuthClientsData,
  type IAMOAuthClientsVariables,
  type IAMUpdateOAuthClientData,
  type IAMUpdateVendorData,
  type IAMUser,
  type IAMUsersData,
  type IAMUsersVariables,
  type IAMVendorInputVariables,
  type IAMVendorOptionsData,
  type IAMVendorOptionsVariables,
  type IAMVendorSummary,
} from "../documents";
import { userLabel } from "../identity-labels";

const SUMMARY_LIMIT = 8;
const MANAGEMENT_LIMIT = 500;

type DialogKind = "vendor" | "provider" | "account" | null;
type ProviderResolutionPolicy =
  | "linked_accounts"
  | "link_by_email"
  | "create_users"
  | "create_users_and_link_by_email";

interface VendorFormState {
  id: string;
  slug: string;
  displayName: string;
  websiteUrl: string;
  icon: string;
  description: string;
}

interface ProviderFormState {
  id: string;
  vendor: string;
  displayName: string;
  clientId: string;
  clientSecret: string;
  environment: string;
  issuer: string;
  authorizeEndpoint: string;
  tokenEndpoint: string;
  revokeEndpoint: string;
  userinfoEndpoint: string;
  jwksUri: string;
  discoveryUrl: string;
  isOidc: boolean;
  isEnabled: boolean;
  supportsRefresh: boolean;
  refreshRotates: boolean;
  supportsPkce: boolean;
  maxRefreshAgeSeconds: string;
  linkOnEmailMatch: boolean;
  createOnLogin: boolean;
  scopesCatalogue: string;
  defaultScopes: string;
  allowedEmailDomains: string;
}

interface ExternalAccountFormState {
  vendor: string;
  externalId: string;
  owner: string;
  email: string;
  displayName: string;
  avatarUrl: string;
  status: string;
}

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

const externalAccountColumns: readonly ListColumn<IAMExternalAccountSummary>[] = [
  {
    field: "displayName",
    header: "Account",
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-fg">
          {row.displayName || row.email || row.externalId}
        </span>
        <Code truncate variant="muted" className="text-2xs">
          {row.externalId}
        </Code>
      </span>
    ),
  },
  {
    field: "vendor.displayName",
    header: "Vendor",
    render: (row) => row.vendor.displayName || row.vendor.slug,
  },
  { field: "email", header: "Email" },
  {
    field: "status",
    header: "Status",
    render: (row) => (
      <Badge variant={statusVariant(row.status)}>{row.status}</Badge>
    ),
  },
  {
    field: "credentialStatus",
    header: "Credential",
    render: (row) => (
      <Badge variant={statusVariant(row.credentialStatus || "unknown")}>
        {row.credentialStatus || "None"}
      </Badge>
    ),
  },
];

const accountStatusOptions = [
  { value: "active", label: "Active" },
  { value: "expired", label: "Expired" },
  { value: "revoked", label: "Revoked" },
  { value: "error", label: "Error" },
  { value: "disabled", label: "Disabled" },
];

const providerResolutionPolicyOptions = [
  { value: "linked_accounts", label: "Linked accounts only" },
  { value: "link_by_email", label: "Link by email match" },
  { value: "create_users", label: "Create users on login" },
  {
    value: "create_users_and_link_by_email",
    label: "Create users and link by email",
  },
];

export function ConnectionsPage(): ReactElement {
  const toast = useToast();
  const variables = useMemo<IAMOAuthClientsVariables>(
    () => ({ pagination: { offset: 0, limit: MANAGEMENT_LIMIT } }),
    [],
  );
  const oauthQuery = useAuthoredQuery<
    IAMOAuthClientsData,
    IAMOAuthClientsVariables
  >(IAM_OAUTH_CLIENTS_QUERY, variables);
  const accountQuery = useAuthoredQuery<
    IAMExternalAccountsData,
    IAMExternalAccountsVariables
  >(IAM_EXTERNAL_ACCOUNTS_QUERY, variables);
  const vendorQuery = useAuthoredQuery<
    IAMVendorOptionsData,
    IAMVendorOptionsVariables
  >(IAM_VENDOR_OPTIONS_QUERY, variables);
  const usersQuery = useAuthoredQuery<IAMUsersData, IAMUsersVariables>(
    IAM_USERS_QUERY,
    variables,
  );
  const [createVendor, createVendorState] = useAuthoredMutation<
    IAMCreateVendorData,
    IAMVendorInputVariables
  >(IAM_CREATE_VENDOR_MUTATION);
  const [updateVendor, updateVendorState] = useAuthoredMutation<
    IAMUpdateVendorData,
    IAMVendorInputVariables
  >(IAM_UPDATE_VENDOR_MUTATION);
  const [createOAuthClient, createOAuthState] = useAuthoredMutation<
    IAMCreateOAuthClientData,
    IAMOAuthClientInputVariables
  >(IAM_CREATE_OAUTH_CLIENT_MUTATION);
  const [updateOAuthClient, updateOAuthState] = useAuthoredMutation<
    IAMUpdateOAuthClientData,
    IAMOAuthClientInputVariables
  >(IAM_UPDATE_OAUTH_CLIENT_MUTATION);
  const [createExternalAccount, createAccountState] = useAuthoredMutation<
    IAMCreateExternalAccountData,
    IAMExternalAccountInputVariables
  >(IAM_CREATE_EXTERNAL_ACCOUNT_MUTATION);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [vendorForm, setVendorForm] = useState<VendorFormState>(() =>
    emptyVendorForm(),
  );
  const [providerForm, setProviderForm] = useState<ProviderFormState>(() =>
    emptyProviderForm(""),
  );
  const [accountForm, setAccountForm] = useState<ExternalAccountFormState>(() =>
    emptyExternalAccountForm(""),
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const vendors = useMemo(
    () => [...(vendorQuery.data?.vendors.results ?? [])],
    [vendorQuery.data],
  );
  const vendorOptions = useMemo(
    () =>
      vendors.map((vendor) => ({
        value: vendor.id,
        label: vendor.displayName || vendor.slug,
      })),
    [vendors],
  );
  const users = useMemo(
    () => [...(usersQuery.data?.users.results ?? [])],
    [usersQuery.data],
  );
  const userOptions = useMemo(
    () =>
      users.map((user) => ({
        value: user.id,
        label: userLabel(user),
      })),
    [users],
  );
  const oauthRows = useMemo(
    () => [...(oauthQuery.data?.oauthClients.results ?? [])],
    [oauthQuery.data],
  );
  const accountRows = useMemo(
    () => [...(accountQuery.data?.externalAccounts.results ?? [])],
    [accountQuery.data],
  );
  const firstVendorId = vendorOptions[0]?.value ?? "";
  const providerPending = createOAuthState.fetching || updateOAuthState.fetching;
  const vendorPending = createVendorState.fetching || updateVendorState.fetching;

  function refetchConnections(): void {
    oauthQuery.refetch();
    accountQuery.refetch();
    vendorQuery.refetch();
    setRefreshVersion((current) => current + 1);
  }

  function closeDialog(): void {
    setDialog(null);
    setActionError(null);
  }

  function openVendor(vendor?: IAMVendorSummary): void {
    setVendorForm(vendor ? vendorFormFromVendor(vendor) : emptyVendorForm());
    setActionError(null);
    setDialog("vendor");
  }

  function openProvider(client?: IAMOAuthClient): void {
    setProviderForm(
      client ? providerFormFromClient(client) : emptyProviderForm(firstVendorId),
    );
    setActionError(null);
    setDialog("provider");
  }

  function openExternalAccount(account?: IAMExternalAccountSummary): void {
    setAccountForm(
      account
        ? externalAccountFormFromAccount(account)
        : emptyExternalAccountForm(firstVendorId),
    );
    setActionError(null);
    setDialog("account");
  }

  async function handleVendorSubmit(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!vendorForm.slug.trim() || !vendorForm.displayName.trim()) {
      setActionError("Vendor slug and display name are required.");
      return;
    }
    setActionError(null);
    try {
      if (vendorForm.id) {
        await updateVendor({ data: vendorPayload(vendorForm, true) });
        toast.success({ title: "Vendor updated" });
      } else {
        await createVendor({ data: vendorPayload(vendorForm, false) });
        toast.success({ title: "Vendor created" });
      }
      closeDialog();
      refetchConnections();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Could not save vendor.",
      );
    }
  }

  async function handleProviderSubmit(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (
      !providerForm.vendor ||
      !providerForm.displayName.trim() ||
      !providerForm.clientId.trim()
    ) {
      setActionError("Provider, display name, and client ID are required.");
      return;
    }
    setActionError(null);
    try {
      if (providerForm.id) {
        await updateOAuthClient({ data: providerPayload(providerForm, true) });
        toast.success({ title: "OIDC provider updated" });
      } else {
        await createOAuthClient({ data: providerPayload(providerForm, false) });
        toast.success({ title: "OIDC provider created" });
      }
      closeDialog();
      refetchConnections();
    } catch (caught) {
      setActionError(
        caught instanceof Error
          ? caught.message
          : "Could not save OIDC provider.",
      );
    }
  }

  async function handleAccountSubmit(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!accountForm.vendor || !accountForm.externalId.trim()) {
      setActionError("Vendor and external ID are required.");
      return;
    }
    setActionError(null);
    try {
      await createExternalAccount({
        data: {
          vendor: accountForm.vendor,
          externalId: accountForm.externalId.trim(),
          owner: accountForm.owner || null,
          email: accountForm.email.trim(),
          displayName: accountForm.displayName.trim(),
          avatarUrl: accountForm.avatarUrl.trim(),
          status: accountForm.status,
        },
      });
      toast.success({ title: "External account saved" });
      closeDialog();
      refetchConnections();
    } catch (caught) {
      setActionError(
        caught instanceof Error
          ? caught.message
          : "Could not save external account.",
      );
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {oauthQuery.error ? (
        <Alert intent="danger" title="OIDC providers unavailable">
          {oauthQuery.error.message}
        </Alert>
      ) : null}
      {accountQuery.error ? (
        <Alert intent="danger" title="External accounts unavailable">
          {accountQuery.error.message}
        </Alert>
      ) : null}
      {vendorQuery.error ? (
        <Alert intent="danger" title="Vendors unavailable">
          {vendorQuery.error.message}
        </Alert>
      ) : null}
      {usersQuery.error ? (
        <Alert intent="danger" title="Users unavailable">
          {usersQuery.error.message}
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button type="button" variant="secondary" onClick={() => openVendor()}>
          <Glyph name="plus" />
          New vendor
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={vendorOptions.length === 0}
          onClick={() => openExternalAccount()}
        >
          <Glyph name="plus" />
          New external account
        </Button>
        <Button
          type="button"
          variant="primary"
          disabled={vendorOptions.length === 0}
          onClick={() => openProvider()}
        >
          <Glyph name="plus" />
          New OIDC provider
        </Button>
      </div>
      <ConnectionSummary
        refreshVersion={refreshVersion}
        onAccountEdit={openExternalAccount}
        onVendorEdit={openVendor}
      />
      <section className="grid gap-2">
        <div className="flex items-center justify-between gap-3">
          <h2 className="m-0 text-sm font-semibold text-fg">OIDC providers</h2>
          <Badge>{oauthRows.length.toLocaleString()}</Badge>
        </div>
        <RowsListView
          rows={oauthRows}
          columns={oauthClientColumns}
          fetching={oauthQuery.fetching}
          error={oauthQuery.error}
          onRowClick={openProvider}
          pageSize={50}
        />
      </section>
      <section className="grid gap-2">
        <div className="flex items-center justify-between gap-3">
          <h2 className="m-0 text-sm font-semibold text-fg">External accounts</h2>
          <Badge>{accountRows.length.toLocaleString()}</Badge>
        </div>
        <RowsListView
          rows={accountRows}
          columns={externalAccountColumns}
          fetching={accountQuery.fetching}
          error={accountQuery.error}
          onRowClick={openExternalAccount}
          pageSize={50}
        />
      </section>
      <VendorDialog
        open={dialog === "vendor"}
        form={vendorForm}
        error={actionError}
        pending={vendorPending}
        onFormChange={setVendorForm}
        onSubmit={handleVendorSubmit}
        onClose={closeDialog}
      />
      <ProviderDialog
        open={dialog === "provider"}
        form={providerForm}
        vendors={vendorOptions}
        error={actionError}
        pending={providerPending}
        onFormChange={setProviderForm}
        onSubmit={handleProviderSubmit}
        onClose={closeDialog}
      />
      <ExternalAccountDialog
        open={dialog === "account"}
        form={accountForm}
        vendors={vendorOptions}
        users={userOptions}
        error={actionError}
        pending={createAccountState.fetching}
        onFormChange={setAccountForm}
        onSubmit={handleAccountSubmit}
        onClose={closeDialog}
      />
    </div>
  );
}

function ConnectionSummary({
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
        {items.length > 0 ? (
          items
        ) : (
          <p className="m-0 py-3 text-13 text-fg-muted">No records.</p>
        )}
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
      <Badge variant={statusVariant(account.credentialStatus)}>
        {account.credentialStatus || "None"}
      </Badge>
    </button>
  );
}

function CredentialSummaryRow({
  credential,
}: {
  credential: {
    id: string;
    kind: string;
    status: string;
    oauthClient: { displayName: string };
    externalAccount: { email: string } | null;
  };
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
      <Badge variant={statusVariant(credential.status)}>
        {credential.status}
      </Badge>
    </div>
  );
}

function VendorDialog({
  open,
  form,
  error,
  pending,
  onFormChange,
  onSubmit,
  onClose,
}: {
  open: boolean;
  form: VendorFormState;
  error: string | null;
  pending: boolean;
  onFormChange: (form: VendorFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}): ReactElement {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="md" placement="center">
          <form onSubmit={onSubmit}>
            <Dialog.Header>
              <Dialog.Title>
                {form.id ? "Edit vendor" : "New vendor"}
              </Dialog.Title>
              <Dialog.Close />
            </Dialog.Header>
            <Dialog.Body className="grid gap-3">
              {error ? (
                <Alert intent="danger" title="Vendor not saved">
                  {error}
                </Alert>
              ) : null}
              <TextField
                label="Slug"
                value={form.slug}
                required
                onChange={(slug) => onFormChange({ ...form, slug })}
              />
              <TextField
                label="Display name"
                value={form.displayName}
                required
                onChange={(displayName) =>
                  onFormChange({ ...form, displayName })
                }
              />
              <TextField
                label="Website URL"
                value={form.websiteUrl}
                onChange={(websiteUrl) =>
                  onFormChange({ ...form, websiteUrl })
                }
              />
              <TextField
                label="Icon"
                value={form.icon}
                onChange={(icon) => onFormChange({ ...form, icon })}
              />
              <TextareaField
                label="Description"
                value={form.description}
                onChange={(description) => onFormChange({ ...form, description })}
              />
            </Dialog.Body>
            <Dialog.Footer>
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" pending={pending}>
                {form.id ? "Save vendor" : "Create vendor"}
              </Button>
            </Dialog.Footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ProviderDialog({
  open,
  form,
  vendors,
  error,
  pending,
  onFormChange,
  onSubmit,
  onClose,
}: {
  open: boolean;
  form: ProviderFormState;
  vendors: readonly { value: string; label: ReactNode }[];
  error: string | null;
  pending: boolean;
  onFormChange: (form: ProviderFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}): ReactElement {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg" placement="center">
          <form onSubmit={onSubmit}>
            <Dialog.Header>
              <Dialog.Title>
                {form.id ? "Edit OIDC provider" : "New OIDC provider"}
              </Dialog.Title>
              <Dialog.Close />
            </Dialog.Header>
            <Dialog.Body className="grid gap-4">
              {error ? (
                <Alert intent="danger" title="OIDC provider not saved">
                  {error}
                </Alert>
              ) : null}
              <div className="grid gap-3 md:grid-cols-2">
                <SelectField
                  label="Vendor"
                  value={form.vendor}
                  options={vendors}
                  required
                  onChange={(vendor) => onFormChange({ ...form, vendor })}
                />
                <TextField
                  label="Environment"
                  value={form.environment}
                  required
                  onChange={(environment) =>
                    onFormChange({ ...form, environment })
                  }
                />
                <TextField
                  label="Display name"
                  value={form.displayName}
                  required
                  onChange={(displayName) =>
                    onFormChange({ ...form, displayName })
                  }
                />
                <TextField
                  label="Client ID"
                  value={form.clientId}
                  required
                  onChange={(clientId) => onFormChange({ ...form, clientId })}
                />
                <TextField
                  label="Client secret"
                  value={form.clientSecret}
                  onChange={(clientSecret) =>
                    onFormChange({ ...form, clientSecret })
                  }
                />
                <TextField
                  label="Issuer"
                  value={form.issuer}
                  onChange={(issuer) => onFormChange({ ...form, issuer })}
                />
                <TextField
                  label="Discovery URL"
                  value={form.discoveryUrl}
                  onChange={(discoveryUrl) =>
                    onFormChange({ ...form, discoveryUrl })
                  }
                />
                <TextField
                  label="Authorize endpoint"
                  value={form.authorizeEndpoint}
                  onChange={(authorizeEndpoint) =>
                    onFormChange({ ...form, authorizeEndpoint })
                  }
                />
                <TextField
                  label="Token endpoint"
                  value={form.tokenEndpoint}
                  onChange={(tokenEndpoint) =>
                    onFormChange({ ...form, tokenEndpoint })
                  }
                />
                <TextField
                  label="Userinfo endpoint"
                  value={form.userinfoEndpoint}
                  onChange={(userinfoEndpoint) =>
                    onFormChange({ ...form, userinfoEndpoint })
                  }
                />
                <TextField
                  label="JWKS URI"
                  value={form.jwksUri}
                  onChange={(jwksUri) => onFormChange({ ...form, jwksUri })}
                />
                <TextField
                  label="Revoke endpoint"
                  value={form.revokeEndpoint}
                  onChange={(revokeEndpoint) =>
                    onFormChange({ ...form, revokeEndpoint })
                  }
                />
                <TextField
                  label="Max refresh age seconds"
                  type="number"
                  value={form.maxRefreshAgeSeconds}
                  onChange={(maxRefreshAgeSeconds) =>
                    onFormChange({ ...form, maxRefreshAgeSeconds })
                  }
                />
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <CheckboxField
                  label="Enabled"
                  checked={form.isEnabled}
                  onChange={(isEnabled) =>
                    onFormChange({ ...form, isEnabled })
                  }
                />
                <CheckboxField
                  label="OIDC"
                  checked={form.isOidc}
                  onChange={(isOidc) => onFormChange({ ...form, isOidc })}
                />
                <CheckboxField
                  label="PKCE"
                  checked={form.supportsPkce}
                  onChange={(supportsPkce) =>
                    onFormChange({ ...form, supportsPkce })
                  }
                />
                <CheckboxField
                  label="Refresh tokens"
                  checked={form.supportsRefresh}
                  onChange={(supportsRefresh) =>
                    onFormChange({ ...form, supportsRefresh })
                  }
                />
                <CheckboxField
                  label="Refresh rotates"
                  checked={form.refreshRotates}
                  onChange={(refreshRotates) =>
                    onFormChange({ ...form, refreshRotates })
                  }
                />
                <SelectField
                  label="User resolution"
                  value={providerResolutionPolicy(form)}
                  options={providerResolutionPolicyOptions}
                  onChange={(policy) =>
                    onFormChange(providerFormWithResolutionPolicy(form, policy))
                  }
                />
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <TextareaField
                  label="Scopes catalogue"
                  value={form.scopesCatalogue}
                  onChange={(scopesCatalogue) =>
                    onFormChange({ ...form, scopesCatalogue })
                  }
                />
                <TextareaField
                  label="Default scopes"
                  value={form.defaultScopes}
                  onChange={(defaultScopes) =>
                    onFormChange({ ...form, defaultScopes })
                  }
                />
                <TextareaField
                  label="Allowed email domains"
                  value={form.allowedEmailDomains}
                  onChange={(allowedEmailDomains) =>
                    onFormChange({ ...form, allowedEmailDomains })
                  }
                />
              </div>
            </Dialog.Body>
            <Dialog.Footer>
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" pending={pending}>
                {form.id ? "Save provider" : "Create provider"}
              </Button>
            </Dialog.Footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ExternalAccountDialog({
  open,
  form,
  vendors,
  users,
  error,
  pending,
  onFormChange,
  onSubmit,
  onClose,
}: {
  open: boolean;
  form: ExternalAccountFormState;
  vendors: readonly { value: string; label: ReactNode }[];
  users: readonly { value: string; label: ReactNode }[];
  error: string | null;
  pending: boolean;
  onFormChange: (form: ExternalAccountFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}): ReactElement {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="md" placement="center">
          <form onSubmit={onSubmit}>
            <Dialog.Header>
              <Dialog.Title>External account</Dialog.Title>
              <Dialog.Close />
            </Dialog.Header>
            <Dialog.Body className="grid gap-3">
              {error ? (
                <Alert intent="danger" title="External account not saved">
                  {error}
                </Alert>
              ) : null}
              <SelectField
                label="Vendor"
                value={form.vendor}
                options={vendors}
                required
                onChange={(vendor) => onFormChange({ ...form, vendor })}
              />
              <SelectField
                label="Owner"
                value={form.owner}
                options={users}
                placeholder="No owner"
                onChange={(owner) => onFormChange({ ...form, owner })}
              />
              <TextField
                label="External ID"
                value={form.externalId}
                required
                onChange={(externalId) =>
                  onFormChange({ ...form, externalId })
                }
              />
              <TextField
                label="Email"
                value={form.email}
                onChange={(email) => onFormChange({ ...form, email })}
              />
              <TextField
                label="Display name"
                value={form.displayName}
                onChange={(displayName) =>
                  onFormChange({ ...form, displayName })
                }
              />
              <TextField
                label="Avatar URL"
                value={form.avatarUrl}
                onChange={(avatarUrl) =>
                  onFormChange({ ...form, avatarUrl })
                }
              />
              <SelectField
                label="Status"
                value={form.status}
                options={accountStatusOptions}
                onChange={(status) => onFormChange({ ...form, status })}
              />
            </Dialog.Body>
            <Dialog.Footer>
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" pending={pending}>
                Save external account
              </Button>
            </Dialog.Footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function TextField({
  label,
  value,
  onChange,
  required = false,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  type?: string;
}): ReactElement {
  return (
    <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
      {label}
      <Input
        type={type}
        value={value}
        required={required}
        aria-label={label}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </label>
  );
}

function TextareaField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}): ReactElement {
  return (
    <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
      {label}
      <Textarea
        value={value}
        aria-label={label}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </label>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
  placeholder,
  required = false,
}: {
  label: string;
  value: string;
  options: readonly { value: string; label: ReactNode }[];
  onChange: (value: string) => void;
  placeholder?: ReactNode;
  required?: boolean;
}): ReactElement {
  return (
    <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
      {label}
      <Select
        value={value}
        options={options}
        placeholder={placeholder ?? `Select ${label.toLowerCase()}`}
        required={required}
        aria-label={label}
        onValueChange={onChange}
      />
    </label>
  );
}

function CheckboxField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}): ReactElement {
  return (
    <Checkbox
      checked={checked}
      onCheckedChange={(next) => onChange(Boolean(next))}
      className="justify-start"
    >
      {label}
    </Checkbox>
  );
}

function emptyVendorForm(): VendorFormState {
  return {
    id: "",
    slug: "",
    displayName: "",
    websiteUrl: "",
    icon: "",
    description: "",
  };
}

function vendorFormFromVendor(vendor: IAMVendorSummary): VendorFormState {
  return {
    id: vendor.id,
    slug: vendor.slug,
    displayName: vendor.displayName,
    websiteUrl: vendor.websiteUrl,
    icon: vendor.icon,
    description: vendor.description,
  };
}

function emptyProviderForm(vendor: string): ProviderFormState {
  return {
    id: "",
    vendor,
    displayName: "",
    clientId: "",
    clientSecret: "",
    environment: "prod",
    issuer: "",
    authorizeEndpoint: "",
    tokenEndpoint: "",
    revokeEndpoint: "",
    userinfoEndpoint: "",
    jwksUri: "",
    discoveryUrl: "",
    isOidc: true,
    isEnabled: true,
    supportsRefresh: true,
    refreshRotates: false,
    supportsPkce: true,
    maxRefreshAgeSeconds: "",
    linkOnEmailMatch: false,
    createOnLogin: false,
    scopesCatalogue: "openid\nemail\nprofile",
    defaultScopes: "openid\nemail\nprofile",
    allowedEmailDomains: "",
  };
}

function providerFormFromClient(client: IAMOAuthClient): ProviderFormState {
  return {
    id: client.id,
    vendor: client.vendor.id,
    displayName: client.displayName,
    clientId: client.clientId,
    clientSecret: client.clientSecret,
    environment: client.environment,
    issuer: client.issuer,
    authorizeEndpoint: client.authorizeEndpoint,
    tokenEndpoint: client.tokenEndpoint,
    revokeEndpoint: client.revokeEndpoint,
    userinfoEndpoint: client.userinfoEndpoint,
    jwksUri: client.jwksUri,
    discoveryUrl: client.discoveryUrl,
    isOidc: client.isOidc,
    isEnabled: client.isEnabled,
    supportsRefresh: client.supportsRefresh,
    refreshRotates: client.refreshRotates,
    supportsPkce: client.supportsPkce,
    maxRefreshAgeSeconds:
      client.maxRefreshAgeSeconds == null ? "" : String(client.maxRefreshAgeSeconds),
    linkOnEmailMatch: client.linkOnEmailMatch,
    createOnLogin: client.createOnLogin,
    scopesCatalogue: client.scopesCatalogue.join("\n"),
    defaultScopes: client.defaultScopes.join("\n"),
    allowedEmailDomains: client.allowedEmailDomains.join("\n"),
  };
}

function providerResolutionPolicy(
  form: ProviderFormState,
): ProviderResolutionPolicy {
  if (form.linkOnEmailMatch && form.createOnLogin) {
    return "create_users_and_link_by_email";
  }
  if (form.linkOnEmailMatch) return "link_by_email";
  if (form.createOnLogin) return "create_users";
  return "linked_accounts";
}

function providerFormWithResolutionPolicy(
  form: ProviderFormState,
  policy: string,
): ProviderFormState {
  switch (policy) {
    case "link_by_email":
      return { ...form, linkOnEmailMatch: true, createOnLogin: false };
    case "create_users":
      return { ...form, linkOnEmailMatch: false, createOnLogin: true };
    case "create_users_and_link_by_email":
      return { ...form, linkOnEmailMatch: true, createOnLogin: true };
    default:
      return { ...form, linkOnEmailMatch: false, createOnLogin: false };
  }
}

function emptyExternalAccountForm(vendor: string): ExternalAccountFormState {
  return {
    vendor,
    externalId: "",
    owner: "",
    email: "",
    displayName: "",
    avatarUrl: "",
    status: "active",
  };
}

function externalAccountFormFromAccount(
  account: IAMExternalAccountSummary,
): ExternalAccountFormState {
  return {
    vendor: account.vendor.id,
    externalId: account.externalId,
    owner: "",
    email: account.email,
    displayName: account.displayName,
    avatarUrl: account.avatarUrl,
    status: account.status.toLowerCase(),
  };
}

function vendorPayload(
  form: VendorFormState,
  includeId: boolean,
): IAMVendorInputVariables["data"] {
  return {
    ...(includeId ? { id: form.id } : {}),
    slug: form.slug.trim(),
    displayName: form.displayName.trim(),
    websiteUrl: form.websiteUrl.trim(),
    icon: form.icon.trim(),
    description: form.description.trim(),
  };
}

function providerPayload(
  form: ProviderFormState,
  includeId: boolean,
): IAMOAuthClientInputVariables["data"] {
  const clientSecret = form.clientSecret.trim();
  return {
    ...(includeId ? { id: form.id } : {}),
    clientSecret,
    vendor: form.vendor,
    displayName: form.displayName.trim(),
    clientId: form.clientId.trim(),
    environment: form.environment.trim() || "prod",
    issuer: form.issuer.trim(),
    authorizeEndpoint: form.authorizeEndpoint.trim(),
    tokenEndpoint: form.tokenEndpoint.trim(),
    revokeEndpoint: form.revokeEndpoint.trim(),
    userinfoEndpoint: form.userinfoEndpoint.trim(),
    jwksUri: form.jwksUri.trim(),
    discoveryUrl: form.discoveryUrl.trim(),
    isOidc: form.isOidc,
    isEnabled: form.isEnabled,
    scopesCatalogue: listValue(form.scopesCatalogue),
    defaultScopes: listValue(form.defaultScopes),
    supportsRefresh: form.supportsRefresh,
    refreshRotates: form.refreshRotates,
    supportsPkce: form.supportsPkce,
    maxRefreshAgeSeconds: optionalPositiveInteger(form.maxRefreshAgeSeconds),
    linkOnEmailMatch: form.linkOnEmailMatch,
    createOnLogin: form.createOnLogin,
    allowedEmailDomains: listValue(form.allowedEmailDomains),
  };
}

function listValue(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function optionalPositiveInteger(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) && number >= 0 ? Math.floor(number) : null;
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
