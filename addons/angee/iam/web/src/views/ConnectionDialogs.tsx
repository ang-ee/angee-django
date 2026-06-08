import {
  type ComponentType,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  Alert,
  Button,
  Dialog,
  DialogForm,
  FieldRow,
  useResolvedWidget,
  type DialogSize,
  type FormField,
  type GroupDescriptor,
  type WidgetOption,
  type WidgetRenderProps,
} from "@angee/base";

import type { IAMExternalAccountSummary } from "../documents";

const accountStatusOptions = [
  { value: "active", label: "Active" },
  { value: "expired", label: "Expired" },
  { value: "revoked", label: "Revoked" },
  { value: "error", label: "Error" },
  { value: "disabled", label: "Disabled" },
] satisfies readonly WidgetOption[];

export interface ExternalAccountFormState {
  vendor: string;
  owner: string;
  externalId: string;
  email: string;
  displayName: string;
  avatarUrl: string;
  status: string;
}

export function ResourceFormDialog({
  open,
  title,
  size = "md",
  children,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  size?: DialogSize;
  children: ReactNode;
  onClose: () => void;
}): ReactElement {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size={size} placement="center">
          <Dialog.Header>
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close />
          </Dialog.Header>
          <Dialog.Body className="p-0">{children}</Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ExternalAccountDialog({
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
  vendors: readonly WidgetOption[];
  users: readonly WidgetOption[];
  error: string | null;
  pending: boolean;
  onFormChange: (form: ExternalAccountFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}): ReactElement {
  return (
    <DialogForm
      open={open}
      onOpenChange={(next) => !next && onClose()}
      onSubmit={onSubmit}
      title="External account"
      size="md"
      placement="center"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" pending={pending}>
            Save external account
          </Button>
        </>
      }
    >
      {error ? (
        <div className="col-span-full">
          <Alert intent="danger" title="External account not saved">
            {error}
          </Alert>
        </div>
      ) : null}
      <WidgetField
        name="vendor"
        label="Vendor"
        widget="many2one"
        value={form.vendor}
        options={vendors}
        required
        onChange={(vendor) => onFormChange({ ...form, vendor })}
      />
      <WidgetField
        name="owner"
        label="Owner"
        widget="many2one"
        value={form.owner}
        options={users}
        onChange={(owner) => onFormChange({ ...form, owner })}
      />
      <WidgetField
        name="externalId"
        label="External ID"
        value={form.externalId}
        required
        onChange={(externalId) => onFormChange({ ...form, externalId })}
      />
      <WidgetField
        name="email"
        label="Email"
        widget="email"
        value={form.email}
        onChange={(email) => onFormChange({ ...form, email })}
      />
      <WidgetField
        name="displayName"
        label="Display name"
        value={form.displayName}
        onChange={(displayName) => onFormChange({ ...form, displayName })}
      />
      <WidgetField
        name="avatarUrl"
        label="Avatar URL"
        widget="url"
        value={form.avatarUrl}
        onChange={(avatarUrl) => onFormChange({ ...form, avatarUrl })}
      />
      <WidgetField
        name="status"
        label="Status"
        widget="statusBadge"
        value={form.status}
        options={accountStatusOptions}
        onChange={(status) => onFormChange({ ...form, status })}
      />
    </DialogForm>
  );
}

function WidgetField({
  name,
  label,
  value,
  options,
  widget = "text",
  required = false,
  onChange,
}: {
  name: keyof ExternalAccountFormState;
  label: string;
  value: string;
  options?: readonly WidgetOption[];
  widget?: string;
  required?: boolean;
  onChange: (value: string) => void;
}): ReactElement {
  const definition = useResolvedWidget(widget);
  const Control = (definition?.edit ?? definition?.read) as
    | ComponentType<WidgetRenderProps<string>>
    | undefined;
  return (
    <FieldRow label={label} required={required}>
      {Control ? (
        <Control
          value={value}
          field={{ name, label, options }}
          onChange={(next) => onChange(String(next ?? ""))}
        />
      ) : null}
    </FieldRow>
  );
}

export function vendorFormFields(): readonly FormField[] {
  return [
    { name: "displayName", label: "Display name", title: true },
    { name: "slug", label: "Slug" },
    { name: "websiteUrl", label: "Website URL", widget: "url" },
    { name: "icon", label: "Icon" },
    { name: "description", label: "Description", widget: "textarea", body: true },
  ];
}

export function providerFormGroups(): readonly GroupDescriptor[] {
  return [
    {
      label: "Client",
      columns: 2,
      actions: [],
      fields: [
        { name: "displayName", label: "Display name", title: true },
        // No options/widget: a nested object relation, so FormView auto-wires
        // the searchable picker and the inline "Create vendor" affordance.
        { name: "vendor", label: "Vendor" },
        { name: "environment", label: "Environment" },
        { name: "clientId", label: "Client ID" },
        { name: "clientSecret", label: "Client secret" },
        { name: "issuer", label: "Issuer" },
        { name: "discoveryUrl", label: "Discovery URL", widget: "url" },
      ],
    },
    {
      label: "Endpoints",
      columns: 2,
      actions: [],
      fields: [
        { name: "authorizeEndpoint", label: "Authorize endpoint", widget: "url" },
        { name: "tokenEndpoint", label: "Token endpoint", widget: "url" },
        { name: "userinfoEndpoint", label: "Userinfo endpoint", widget: "url" },
        { name: "jwksUri", label: "JWKS URI", widget: "url" },
        { name: "revokeEndpoint", label: "Revoke endpoint", widget: "url" },
      ],
    },
    {
      label: "Policy",
      columns: 2,
      actions: [],
      fields: [
        { name: "isEnabled", label: "Enabled", widget: "switch" },
        { name: "isOidc", label: "OIDC", widget: "switch" },
        { name: "supportsPkce", label: "PKCE", widget: "switch" },
        { name: "supportsRefresh", label: "Refresh tokens", widget: "switch" },
        { name: "refreshRotates", label: "Refresh rotates", widget: "switch" },
        { name: "linkOnEmailMatch", label: "Link by email match", widget: "switch" },
        { name: "createOnLogin", label: "Create users on login", widget: "switch" },
        {
          name: "maxRefreshAgeSeconds",
          label: "Max refresh age seconds",
          widget: "integer",
        },
        { name: "scopesCatalogue", label: "Scopes catalogue", widget: "tagInput" },
        { name: "defaultScopes", label: "Default scopes", widget: "tagInput" },
        {
          name: "allowedEmailDomains",
          label: "Allowed email domains",
          widget: "tagInput",
        },
      ],
    },
  ];
}

export function providerDefaultValues(vendor: string): Record<string, unknown> {
  return {
    vendor,
    environment: "prod",
    isOidc: true,
    isEnabled: true,
    supportsRefresh: true,
    refreshRotates: false,
    supportsPkce: true,
    linkOnEmailMatch: false,
    createOnLogin: false,
    maxRefreshAgeSeconds: null,
    scopesCatalogue: ["openid", "email", "profile"],
    defaultScopes: ["openid", "email", "profile"],
    allowedEmailDomains: [],
  };
}

export function emptyExternalAccountForm(vendor: string): ExternalAccountFormState {
  return {
    vendor,
    owner: "",
    externalId: "",
    email: "",
    displayName: "",
    avatarUrl: "",
    status: "active",
  };
}

export function externalAccountFormFromAccount(
  account: IAMExternalAccountSummary,
): ExternalAccountFormState {
  return {
    vendor: account.vendor.id,
    owner: "",
    externalId: account.externalId,
    email: account.email,
    displayName: account.displayName,
    avatarUrl: account.avatarUrl,
    status: account.status.toLowerCase(),
  };
}
