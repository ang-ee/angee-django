import {
  useMemo,
  useState,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  Alert,
  Badge,
  Button,
  FormView,
  Glyph,
  RowsListView,
  useToast,
} from "@angee/base";
import {
  useResourceList,
  useResourceMutation,
  type Row,
} from "@angee/sdk";

import type {
  IAMExternalAccountSummary,
  IAMOAuthClient,
  IAMVendorSummary,
} from "../documents";
import { userLabel } from "../identity-labels";
import { IAM_LIST_LIMIT } from "../list-config";
import {
  ExternalAccountDialog,
  ResourceFormDialog,
  emptyExternalAccountForm,
  externalAccountFormFromAccount,
  providerDefaultValues,
  providerFormGroups,
  vendorFormFields,
  type ExternalAccountFormState,
} from "./ConnectionDialogs";
import {
  externalAccountColumns,
  oauthClientColumns,
} from "./ConnectionListColumns";
import { ConnectionSummary } from "./ConnectionSummary";

const VENDOR_MODEL = "Vendor";
const OAUTH_CLIENT_MODEL = "OAuthClient";
const EXTERNAL_ACCOUNT_MODEL = "ExternalAccount";
const USER_MODEL = "User";

const VENDOR_FIELDS = [
  "slug",
  "displayName",
  "websiteUrl",
  "icon",
  "description",
] as const;
const USER_OPTION_FIELDS = ["username", "email"] as const;
const OAUTH_CLIENT_FIELDS = [
  "displayName",
  "vendor.id",
  "vendor.slug",
  "vendor.displayName",
  "vendorLabel",
  "vendorSlug",
  "environment",
  "clientId",
  "clientSecret",
  "issuer",
  "authorizeEndpoint",
  "tokenEndpoint",
  "revokeEndpoint",
  "userinfoEndpoint",
  "jwksUri",
  "discoveryUrl",
  "isOidc",
  "isEnabled",
  "configurationState",
  "supportsRefresh",
  "refreshRotates",
  "supportsPkce",
  "maxRefreshAgeSeconds",
  "linkOnEmailMatch",
  "createOnLogin",
  "scopesCatalogue",
  "defaultScopes",
  "allowedEmailDomains",
] as const;
const EXTERNAL_ACCOUNT_FIELDS = [
  "externalId",
  "email",
  "displayName",
  "avatarUrl",
  "status",
  "credentialStatus",
  "lastUsedAt",
  "vendor.id",
  "vendor.slug",
  "vendor.displayName",
] as const;

type DialogKind = "vendor" | "provider" | "account" | null;

interface UserOptionRow extends Row {
  id: string;
  username: string;
  email: string;
}

export function ConnectionsPage(): ReactElement {
  const toast = useToast();
  const vendors = useResourceList(VENDOR_MODEL, {
    fields: VENDOR_FIELDS,
    pageSize: IAM_LIST_LIMIT,
  });
  const users = useResourceList(USER_MODEL, {
    fields: USER_OPTION_FIELDS,
    pageSize: IAM_LIST_LIMIT,
  });
  const oauthClients = useResourceList(OAUTH_CLIENT_MODEL, {
    fields: OAUTH_CLIENT_FIELDS,
    pageSize: IAM_LIST_LIMIT,
  });
  const externalAccounts = useResourceList(EXTERNAL_ACCOUNT_MODEL, {
    fields: EXTERNAL_ACCOUNT_FIELDS,
    pageSize: IAM_LIST_LIMIT,
  });
  const [createExternalAccount, createAccountState] = useResourceMutation(
    EXTERNAL_ACCOUNT_MODEL,
    "create",
    { fields: EXTERNAL_ACCOUNT_FIELDS },
  );
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [vendorId, setVendorId] = useState<string | null>(null);
  const [providerId, setProviderId] = useState<string | null>(null);
  const [accountForm, setAccountForm] = useState<ExternalAccountFormState>(() =>
    emptyExternalAccountForm(""),
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);

  const vendorOptions = useMemo(
    () =>
      (vendors.rows as unknown as readonly IAMVendorSummary[]).map((vendor) => ({
        value: vendor.id,
        label: vendor.displayName || vendor.slug,
      })),
    [vendors.rows],
  );
  const userOptions = useMemo(
    () =>
      (users.rows as unknown as readonly UserOptionRow[]).map((user) => ({
        value: String(user.id),
        label: userLabel(user),
      })),
    [users.rows],
  );
  const firstVendorId = vendorOptions[0]?.value ?? "";
  const providerGroups = useMemo(() => providerFormGroups(), []);
  const providerDefaults = useMemo(
    () => providerDefaultValues(firstVendorId),
    [firstVendorId],
  );

  function refetchConnections(): void {
    vendors.refetch();
    oauthClients.refetch();
    externalAccounts.refetch();
    setRefreshVersion((current) => current + 1);
  }

  function closeDialog(): void {
    setDialog(null);
    setActionError(null);
  }

  function openVendor(vendor?: IAMVendorSummary): void {
    setVendorId(vendor?.id ?? null);
    setDialog("vendor");
  }

  function openProvider(client?: IAMOAuthClient): void {
    setProviderId(client?.id ?? null);
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

  async function handleAccountSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
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
        caught instanceof Error ? caught.message : "Could not save external account.",
      );
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <OptionsError vendors={vendors.error} users={users.error} />
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
      <ManagementSection
        title="OIDC providers"
        total={oauthClients.total ?? oauthClients.rows.length}
      >
        <RowsListView
          rows={oauthClients.rows as unknown as readonly IAMOAuthClient[]}
          columns={oauthClientColumns}
          fetching={oauthClients.fetching}
          error={oauthClients.error}
          onRowClick={openProvider}
          pageSize={50}
        />
      </ManagementSection>
      <ManagementSection
        title="External accounts"
        total={externalAccounts.total ?? externalAccounts.rows.length}
      >
        <RowsListView
          rows={externalAccounts.rows as unknown as readonly IAMExternalAccountSummary[]}
          columns={externalAccountColumns}
          fetching={externalAccounts.fetching}
          error={externalAccounts.error}
          onRowClick={openExternalAccount}
          pageSize={50}
        />
      </ManagementSection>
      <ResourceFormDialog
        open={dialog === "vendor"}
        title={vendorId ? "Edit vendor" : "New vendor"}
        onClose={closeDialog}
      >
        <FormView
          model={VENDOR_MODEL}
          id={vendorId}
          fields={vendorFormFields()}
          returning={VENDOR_FIELDS}
          submitLabel={vendorId ? "Save vendor" : "Create vendor"}
          onSaved={() =>
            handleResourceSaved(vendorId ? "Vendor updated" : "Vendor created")
          }
        />
      </ResourceFormDialog>
      <ResourceFormDialog
        open={dialog === "provider"}
        title={providerId ? "Edit OIDC provider" : "New OIDC provider"}
        size="lg"
        onClose={closeDialog}
      >
        <FormView
          model={OAUTH_CLIENT_MODEL}
          id={providerId}
          groups={providerGroups}
          returning={OAUTH_CLIENT_FIELDS}
          defaultValues={providerDefaults}
          submitLabel={providerId ? "Save provider" : "Create provider"}
          onSaved={() =>
            handleResourceSaved(
              providerId ? "OIDC provider updated" : "OIDC provider created",
            )
          }
        />
      </ResourceFormDialog>
      <ExternalAccountDialog
        open={dialog === "account"}
        form={accountForm}
        vendors={vendorOptions}
        users={userOptions}
        error={actionError ?? createAccountState.error?.message ?? null}
        pending={createAccountState.fetching}
        onFormChange={setAccountForm}
        onSubmit={handleAccountSubmit}
        onClose={closeDialog}
      />
    </div>
  );

  function handleResourceSaved(title: string): void {
    toast.success({ title });
    closeDialog();
    refetchConnections();
  }
}

function OptionsError({
  vendors,
  users,
}: {
  vendors: Error | null;
  users: Error | null;
}): ReactElement | null {
  if (!vendors && !users) return null;
  return (
    <>
      {vendors ? (
        <Alert intent="danger" title="Vendors unavailable">
          {vendors.message}
        </Alert>
      ) : null}
      {users ? (
        <Alert intent="danger" title="Users unavailable">
          {users.message}
        </Alert>
      ) : null}
    </>
  );
}

function ManagementSection({
  title,
  total,
  children,
}: {
  title: string;
  total: number;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="grid gap-2">
      <div className="flex items-center justify-between gap-3">
        <h2 className="m-0 text-sm font-semibold text-fg">{title}</h2>
        <Badge>{total.toLocaleString()}</Badge>
      </div>
      {children}
    </section>
  );
}
