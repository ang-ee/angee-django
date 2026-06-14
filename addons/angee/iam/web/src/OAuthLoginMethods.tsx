import { Alert, Button, Glyph, Spinner } from "@angee/base";
import { errorMessage, useAuthoredMutation, useAuthoredQuery } from "@angee/sdk";
import { useState, type ReactNode } from "react";

import {
  AVAILABLE_CONNECTIONS_QUERY,
  LOGIN_START_MUTATION,
  type AvailableConnection,
  type AvailableConnectionsData,
  type LoginStartData,
  type LoginStartVariables,
} from "./documents";
import { loginCallbackRedirectUri, loginNextFromLocation } from "./redirects";
import { useIamT } from "./i18n";

export function OAuthLoginMethods(): ReactNode {
  const t = useIamT();
  const { data, fetching, error: queryError } =
    useAuthoredQuery<AvailableConnectionsData>(AVAILABLE_CONNECTIONS_QUERY);
  const [loginStart] = useAuthoredMutation<LoginStartData, LoginStartVariables>(
    LOGIN_START_MUTATION,
  );
  const [startingSqid, setStartingSqid] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const connections = data?.availableConnections.results ?? [];

  async function startLogin(connection: AvailableConnection): Promise<void> {
    setStartError(null);
    setStartingSqid(connection.oauthClientSqid);
    try {
      const result = await loginStart({
        oauthClientSqid: connection.oauthClientSqid,
        redirectUri: loginCallbackRedirectUri(),
        next: loginNextFromLocation(),
      });
      const payload = result?.loginStart;
      if (payload?.authorizeUrl) {
        // Keep the buttons disabled while the full-page redirect lands, so a
        // double-click can't start a second flow during navigation.
        window.location.assign(payload.authorizeUrl);
        return;
      }
      setStartError(payload?.error ?? t("iam.login.startError"));
    } catch (caught) {
      setStartError(errorMessage(caught, t("iam.login.startError")));
    }
    setStartingSqid(null);
  }

  if (fetching && connections.length === 0) {
    return (
      <div
        aria-live="polite"
        className="flex items-center gap-3 rounded-md border border-border-subtle bg-inset px-4 py-3 text-sm text-fg-muted"
        role="status"
      >
        <Spinner size="sm" tone="brand" />
        <span>{t("iam.login.loadingOptions")}</span>
      </div>
    );
  }

  if (queryError && connections.length === 0) {
    return (
      <Alert className="mb-6" tone="danger" title={t("iam.login.providersUnavailable")}>
        {t("iam.login.passwordStillAvailable")}
      </Alert>
    );
  }

  if (connections.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        {connections.map((connection) => {
          const label = connectionLabel(connection);
          const active = startingSqid === connection.oauthClientSqid;
          return (
            <Button
              key={connection.oauthClientSqid}
              type="button"
              className="!h-11 w-full justify-start bg-sheet"
              disabled={startingSqid !== null}
              loading={active}
              loadingText={t("iam.login.continueWith", { provider: label })}
              onClick={() => void startLogin(connection)}
              size="lg"
              variant="secondary"
            >
              <ProviderMark label={label} />
              <span className="min-w-0 truncate">{t("iam.login.continueWith", { provider: label })}</span>
            </Button>
          );
        })}
      </div>
      {startError ? (
        <Alert tone="danger" title={t("iam.login.startFailed")}>
          {startError}
        </Alert>
      ) : null}
    </div>
  );
}

function ProviderMark({ label }: { label: string }): ReactNode {
  const initial = label.trim().charAt(0).toUpperCase();
  return (
    <span
      aria-hidden="true"
      className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border-subtle bg-accent-soft text-xs font-semibold text-accent-soft-text"
    >
      {initial || <Glyph name="auth" className="size-4" />}
    </span>
  );
}

function connectionLabel(connection: AvailableConnection): string {
  return (
    connection.oauthClientDisplayName.trim() ||
    connection.oauthClientSlug.trim() ||
    "provider"
  );
}
