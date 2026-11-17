import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";

import { ConnectOAuthButton, type ConnectOAuthButtonProps, type ConnectStartPayload } from "./ConnectOAuthButton";
import { IntegrateConnectAccountStart } from "./documents.public";

export interface ConnectAccountStartInput {
  /** The OAuth client the account connects through. */
  id: string;
  redirectUri: string;
  next: string;
  /** The scopes of the consumer the account is connected for (its `oauth_scopes`). */
  scopes?: readonly string[];
}

/**
 * Start one account connect at an OAuth client: the start the providers' Connect
 * account verb and {@link ConnectAccountButton} share. The server bounds `scopes`
 * by the client's catalogue and requires the grant to cover them.
 */
export function useConnectAccountStart(): (input: ConnectAccountStartInput) => Promise<ConnectStartPayload | null | undefined> {
  const [connectAccountStart] = useAuthoredMutation(IntegrateConnectAccountStart);
  return React.useCallback(
    async ({ scopes, ...input }: ConnectAccountStartInput) =>
      (await connectAccountStart({ ...input, scopes: [...(scopes ?? [])] }))?.connect_account_start,
    [connectAccountStart],
  );
}

export type ConnectAccountButtonProps = Omit<ConnectOAuthButtonProps, "row" | "start"> & {
  /** The OAuth client the account connects through. */
  oauthClientId: string;
  /** The scopes of the consumer the account is connected for. */
  scopes?: readonly string[];
};

/** Connect another account at one OAuth client, asking for a consumer's scopes, over the shared ceremony. */
export function ConnectAccountButton({ oauthClientId, scopes, ...props }: ConnectAccountButtonProps): React.ReactElement {
  const start = useConnectAccountStart();
  return <ConnectOAuthButton {...props} start={(redirect) => start({ ...redirect, id: oauthClientId, scopes })} />;
}
