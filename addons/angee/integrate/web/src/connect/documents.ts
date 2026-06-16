// Bespoke action mutations for the outbound connect-for-API surface (OAuth account
// connect + credential reveal). Model CRUD for OAuthClient/ExternalAccount/Credential
// is model-driven (DataPage reads the SDL); these are the non-CRUD operations the
// connect views invoke. Inbound OIDC login (loginStart/availableConnections) lives
// in `@angee/iam`.

import type { ByIdVariables } from "@angee/sdk";

export const CONNECT_ACCOUNT_START_MUTATION = `
  mutation IntegrateConnectAccountStart(
    $id: ID!
    $redirectUri: String!
    $next: String!
  ) {
    connectAccountStart(
      id: $id
      redirectUri: $redirectUri
      next: $next
    ) {
      authorizeUrl
      error
      mode
      state
      redirectUri
    }
  }
`;

export const CONNECT_ACCOUNT_COMPLETE_MUTATION = `
  mutation IntegrateConnectAccountComplete(
    $code: String!
    $state: String!
    $redirectUri: String!
  ) {
    connectAccountComplete(code: $code, state: $state, redirectUri: $redirectUri) {
      next
      error
      account { id displayName providerSlug }
      credential { id displayName status }
    }
  }
`;

export const REVEAL_CREDENTIAL_MUTATION = `
  mutation IntegrateRevealCredential($id: ID!) {
    revealCredential(id: $id) {
      secret
    }
  }
`;

/** Selection result for SDL `OAuthStartPayload` (connect-start subset). */
export interface OAuthStartPayload {
  authorizeUrl: string;
  error: string | null;
  /** "auto" (redirect back) or "manual" (paste the code). */
  mode?: string;
  /** The state token, resent at manual completion. */
  state?: string;
  /** The effective redirect URI, resent at completion. */
  redirectUri?: string;
}

/** Selection result for `IntegrateConnectAccountStart`. */
export interface ConnectAccountStartData {
  connectAccountStart: OAuthStartPayload;
}

export type ConnectAccountStartVariables = Record<string, unknown> & {
  id: string;
  redirectUri: string;
  next: string;
};

/** Selection result for SDL `ConnectAccountResult`. */
export interface ConnectAccountCompletePayload {
  next: string;
  error: string | null;
  account: { id: string; displayName: string; providerSlug: string } | null;
  credential: { id: string; displayName: string; status: string } | null;
}

/** Selection result for `IntegrateConnectAccountComplete`. */
export interface ConnectAccountCompleteData {
  connectAccountComplete: ConnectAccountCompletePayload;
}

export type ConnectAccountCompleteVariables = Record<string, unknown> & {
  code: string;
  state: string;
  redirectUri: string;
};

/** Selection result for `IntegrateRevealCredential`. */
export interface RevealCredentialData {
  revealCredential: { secret: string };
}

export type RevealCredentialVariables = ByIdVariables;
