import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { Avatar, Button, ErrorBanner, LoadingPanel, avatarInitials, errorMessage } from "@angee/ui";
import { useState, type ReactNode } from "react";

import {
  IamAvailableConnections,
  IamLoginStart,
  type AvailableConnection,
} from "./documents.public";
import { loginCallbackRedirectUri, loginNextFromLocation } from "./redirects";
import { useOidcT } from "./i18n";

export function OAuthLoginMethods(): ReactNode {
  const t = useOidcT();
  const { data, isFetching: fetching, error: queryError } =
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
    return <LoadingPanel density="inline" message={t("login.loadingOptions")} />;
  }

  if (queryError && connections.length === 0) {
    return (
      <ErrorBanner className="mb-6" title={t("login.providersUnavailable")} description={t("login.passwordStillAvailable")} />
    );
  }

  if (connections.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        {connections.map((connection) => {
          const label = connectionLabel(connection, t("login.providerFallback"));
          const active = startingSqid === connection.oauth_client_sqid;
          return (
            <Button
              key={connection.oauth_client_sqid}
              type="button"
              className="w-full justify-start"
              disabled={startingSqid !== null}
              loading={active}
              loadingText={t("login.continueWith", { provider: label })}
              onClick={() => void startLogin(connection)}
              size="lg"
              variant="secondary"
            >
              <Avatar size="sm" initials={avatarInitials(label)} aria-hidden="true" />
              <span className="min-w-0 truncate">{t("login.continueWith", { provider: label })}</span>
            </Button>
          );
        })}
      </div>
      {startError ? (
        <ErrorBanner title={t("login.startFailed")} description={startError} />
      ) : null}
    </div>
  );
}

function connectionLabel(connection: AvailableConnection, fallback: string): string {
  return (
    connection.oauth_client_display_name.trim() ||
    connection.oauth_client_slug.trim() ||
    fallback
  );
}
