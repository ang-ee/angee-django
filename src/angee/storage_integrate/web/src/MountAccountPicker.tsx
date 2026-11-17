import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  InlineEmpty,
  LoadingPanel,
  NavLink,
  PaneRow,
  PaneRows,
  errorMessage,
  mutationDialogValueCodecs,
  useRouteHref,
  type DescriptorFieldControlProps,
} from "@angee/ui";
import * as React from "react";

import { MountAccounts } from "./documents";
import { useStorageIntegrateT } from "./i18n";

export interface MountAccountPickerProps extends DescriptorFieldControlProps {
  backendClass: string;
}

/**
 * Bare MutationDialog control choosing which of the user's accounts a credential
 * mount backend reads with (`MountManager.connectable_credentials`). Connecting
 * another account is the OAuth client's own Connect account action.
 */
export function MountAccountPicker({
  backendClass,
  readOnly,
  invalid,
  describedBy,
  labelledBy,
  value,
  onChange,
}: MountAccountPickerProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const routeHref = useRouteHref();
  const query = useAuthoredQuery(MountAccounts, { backendClass });
  const result = query.data?.mount_accounts;
  const selected = mutationDialogValueCodecs.string(value) ?? "";
  const accounts = result?.accounts ?? [];

  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
      className="grid gap-3"
    >
      <ErrorBanner
        description={query.error ? errorMessage(query.error, t("mount.account.error")) : null}
      />
      {query.isFetching && accounts.length === 0 ? (
        <LoadingPanel density="inline" loadingLabel={t("mount.account.loading")} />
      ) : accounts.length === 0 ? (
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
      {result?.oauth_client ? (
        <NavLink href={routeHref("integrate.provider", { id: result.oauth_client })} variant="inline">
          {t("mount.account.connect")}
        </NavLink>
      ) : null}
    </div>
  );
}
