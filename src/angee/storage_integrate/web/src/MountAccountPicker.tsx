import { ConnectAccountButton } from "@angee/integrate";
import {
  InlineEmpty,
  PaneRow,
  PaneRows,
  mutationDialogValueCodecs,
  useRouteHref,
  type DescriptorFieldControlProps,
} from "@angee/ui";
import * as React from "react";

import { useStorageIntegrateT } from "./i18n";

export interface MountAccount {
  id: string;
  display_name: string;
}

export interface MountAccountPickerProps extends DescriptorFieldControlProps {
  /** The session user's accounts the backend may read with (`MountManager.connectable_credentials`). */
  accounts: readonly MountAccount[];
  /** The OAuth client another account connects through, when one is ready. */
  oauthClientId: string | null;
  /** The scopes an account must grant for the backend to read with it (`MountBackend.oauth_scopes`). */
  oauthScopes: readonly string[];
  /** Called once an account connected in place (a manual-code provider), so the accounts reload. */
  onConnected?: () => void;
}

/**
 * Bare MutationDialog control choosing which of the user's accounts a credential
 * mount backend reads with, and connecting another one through the backend's OAuth
 * client with the scopes it reads with. A redirect-back provider returns to Mounts.
 */
export function MountAccountPicker({
  accounts,
  oauthClientId,
  oauthScopes,
  onConnected,
  readOnly,
  invalid,
  describedBy,
  labelledBy,
  value,
  onChange,
}: MountAccountPickerProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const routeHref = useRouteHref();
  const selected = mutationDialogValueCodecs.string(value) ?? "";

  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
      className="grid gap-3"
    >
      {accounts.length === 0 ? (
        <InlineEmpty icon="user" label={t("mount.account.empty")} />
      ) : (
        <PaneRows>
          {accounts.map((account) => (
            <li key={account.id}>
              <PaneRow icon="user" title={account.display_name} selected={account.id === selected}
                label={t("mount.account.use")} verb={t("mount.account.use")}
                onOpen={readOnly ? undefined : () => onChange(account.id)} />
            </li>
          ))}
        </PaneRows>
      )}
      {oauthClientId ? (
        <div>
          <ConnectAccountButton oauthClientId={oauthClientId} scopes={oauthScopes}
            next={routeHref("storage-integrate.mounts")} label={t("mount.account.connect")}
            onConnected={onConnected} disabled={readOnly} />
        </div>
      ) : null}
    </div>
  );
}
