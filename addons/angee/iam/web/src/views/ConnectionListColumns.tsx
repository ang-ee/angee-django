import {
  Code,
  type BadgeVariant,
  type ListColumn,
} from "@angee/base";

import type {
  IAMExternalAccountSummary,
  IAMOAuthClient,
} from "../documents";

export const STATUS_TONES: Record<string, BadgeVariant> = {
  active: "success",
  ACTIVE: "success",
  ready: "success",
  READY: "success",
  ok: "success",
  OK: "success",
  valid: "success",
  VALID: "success",
  enabled: "success",
  ENABLED: "success",
  warning: "warning",
  WARNING: "warning",
  pending: "warning",
  PENDING: "warning",
  stale: "warning",
  STALE: "warning",
  error: "danger",
  ERROR: "danger",
  failed: "danger",
  FAILED: "danger",
  expired: "danger",
  EXPIRED: "danger",
  revoked: "danger",
  REVOKED: "danger",
  disabled: "danger",
  DISABLED: "danger",
};

export const oauthClientColumns: readonly ListColumn<IAMOAuthClient>[] = [
  {
    field: "displayName",
    header: "Client",
    render: (row) => <span className="font-medium text-fg">{row.displayName}</span>,
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
    widget: "booleanBadge",
    options: [
      { value: "true", label: "Enabled" },
      { value: "false", label: "Disabled" },
    ],
  },
  {
    field: "configurationState",
    header: "Configuration",
    tone: STATUS_TONES,
  },
  {
    field: "supportsPkce",
    header: "PKCE",
    widget: "booleanBadge",
    options: [
      { value: "true", label: "Supported" },
      { value: "false", label: "Not supported" },
    ],
  },
];

export const externalAccountColumns: readonly ListColumn<IAMExternalAccountSummary>[] = [
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
  { field: "status", header: "Status", tone: STATUS_TONES },
  { field: "credentialStatus", header: "Credential", tone: STATUS_TONES },
];

export function toneFor(value: string | null | undefined): BadgeVariant {
  return value ? STATUS_TONES[value] ?? "default" : "default";
}
