import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { Alert, Button, Glyph, Spinner, errorMessage } from "@angee/ui";
import { useState, type ReactNode } from "react";

import {
  IamAvailableConnections,
  IamLoginStart,
  type AvailableConnection,
} from "./documents.public";
import { loginCallbackRedirectUri, loginNextFromLocation } from "./redirects";
import { useIamT } from "./i18n";

export function OAuthLoginMethods(): ReactNode {
  const t = useIamT();
  const { data, fetching, error: queryError } =
    useAuthoredQuery(IamAvailableConnections);
  const [loginStart] = useAuthoredMutation(IamLoginStart);
  const [startingSqid, setStartingSqid] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const connections = data?.available_connections.results ?? [];

  async function startLogin(connection: AvailableConnection): Promise<void> {
    setStartError(null);
    setStartingSqid(connection.oauth_client_sqid);
    try {
      const result = await loginStart({
        oauthClientSqid: connection.oauth_client_sqid,
        redirectUri: loginCallbackRedirectUri(),
        next: loginNextFromLocation(),
      });
      const payload = result?.login_start;
      if (payload?.authorize_url) {
        // Keep the buttons disabled while the full-page redirect lands, so a
        // double-click can't start a second flow during navigation.
        window.location.assign(payload.authorize_url);
        return;
      }
      setStartError(payload?.error ?? t("login.startError"));
    } catch (caught) {
      setStartError(errorMessage(caught, t("login.startError")));
    }
    setStartingSqid(null);
  }

  if (fetching && connections.length === 0) {
    return (
      <div
        aria-live="polite"
        className="flex items-center gap-3 rounded-6 border border-border-subtle bg-inset px-4 py-3 text-sm text-fg-muted"
        role="status"
      >
        <Spinner size="sm" tone="brand" />
        <span>{t("login.loadingOptions")}</span>
      </div>
    );
  }

  if (queryError && connections.length === 0) {
    return (
      <Alert className="mb-6" tone="danger" title={t("login.providersUnavailable")}>
        {t("login.passwordStillAvailable")}
      </Alert>
    );
  }

  if (connections.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        {connections.map((connection) => {
          const label = connectionLabel(connection);
          const active = startingSqid === connection.oauth_client_sqid;
          return (
            <Button
              key={connection.oauth_client_sqid}
              type="button"
              className="!h-11 w-full justify-start bg-sheet"
              disabled={startingSqid !== null}
              loading={active}
              loadingText={t("login.continueWith", { provider: label })}
              onClick={() => void startLogin(connection)}
              size="lg"
              variant="secondary"
            >
              <ProviderMark label={label} />
              <span className="min-w-0 truncate">{t("login.continueWith", { provider: label })}</span>
            </Button>
          );
        })}
      </div>
      {startError ? (
        <Alert tone="danger" title={t("login.startFailed")}>
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
      className="flex size-7 shrink-0 items-center justify-center rounded-6 border border-border-subtle bg-accent-soft text-xs font-semibold text-accent-soft-text"
    >
      {initial || <Glyph name="auth" className="size-4" />}
    </span>
  );
}

function connectionLabel(connection: AvailableConnection): string {
  return (
    connection.oauth_client_display_name.trim() ||
    connection.oauth_client_slug.trim() ||
    "provider"
  );
}
