import { Alert, Button, Glyph, Spinner } from "@angee/base";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/sdk";
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

export function OAuthLoginMethods(): ReactNode {
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
      setStartError(payload?.error ?? "Could not start sign-in.");
    } catch (caught) {
      setStartError(errorMessage(caught, "Could not start sign-in."));
    }
    setStartingSqid(null);
  }

  if (fetching && connections.length === 0) {
    return (
      <div
        aria-live="polite"
        className="mb-6 flex items-center gap-3 rounded-md border border-border-subtle bg-inset px-4 py-3 text-sm text-fg-muted"
        role="status"
      >
        <Spinner size="sm" tone="brand" />
        <span>Loading sign-in options...</span>
      </div>
    );
  }

  if (queryError && connections.length === 0) {
    return (
      <Alert className="mb-6" intent="danger" title="Sign-in providers unavailable">
        Username and password sign-in is still available.
      </Alert>
    );
  }

  if (connections.length === 0) return null;

  return (
    <div className="mb-6 flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        {connections.map((connection) => {
          const label = connectionLabel(connection);
          const active = startingSqid === connection.oauthClientSqid;
          return (
            <Button
              key={connection.oauthClientSqid}
              type="button"
              className="w-full justify-start"
              disabled={startingSqid !== null}
              loading={active}
              loadingText={`Continue with ${label}`}
              onClick={() => void startLogin(connection)}
              size="lg"
              variant="secondary"
            >
              <ProviderMark label={label} />
              <span className="min-w-0 truncate">Continue with {label}</span>
            </Button>
          );
        })}
      </div>
      {startError ? (
        <Alert intent="danger" title="Sign-in could not start">
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
      className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border-subtle bg-sheet text-xs font-semibold text-fg-muted"
    >
      {initial || <Glyph name="auth" className="size-4" />}
    </span>
  );
}

function connectionLabel(connection: AvailableConnection): string {
  return (
    connection.vendor.displayName.trim() ||
    connection.oauthClientDisplayName.trim() ||
    "provider"
  );
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}
