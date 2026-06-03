export const AVAILABLE_CONNECTIONS_QUERY = `
  query IamAvailableConnections {
    availableConnections {
      results {
        oauthClientSqid
        oauthClientDisplayName
        vendor {
          displayName
        }
      }
    }
  }
`;

export const LOGIN_START_MUTATION = `
  mutation IamLoginStart(
    $oauthClientSqid: String!
    $redirectUri: String!
    $next: String!
  ) {
    loginStart(
      oauthClientSqid: $oauthClientSqid
      redirectUri: $redirectUri
      next: $next
    ) {
      authorizeUrl
      error
    }
  }
`;

export const LOGIN_COMPLETE_MUTATION = `
  mutation IamLoginComplete(
    $code: String!
    $state: String!
    $redirectUri: String!
  ) {
    loginComplete(code: $code, state: $state, redirectUri: $redirectUri) {
      ok
      next
      error
    }
  }
`;

export interface AvailableConnectionVendor {
  displayName: string;
}

export interface AvailableConnection {
  oauthClientSqid: string;
  oauthClientDisplayName: string;
  vendor: AvailableConnectionVendor;
}

export interface AvailableConnectionsData {
  availableConnections: {
    results: AvailableConnection[];
  };
}

export interface OidcStartPayload {
  authorizeUrl: string;
  error: string | null;
}

export interface LoginStartData {
  loginStart: OidcStartPayload;
}

export type LoginStartVariables = Record<string, unknown> & {
  oauthClientSqid: string;
  redirectUri: string;
  next: string;
};

export interface LoginCompletePayload {
  ok: boolean;
  next: string;
  error: string | null;
}

export interface LoginCompleteData {
  loginComplete: LoginCompletePayload;
}

export type LoginCompleteVariables = Record<string, unknown> & {
  code: string;
  state: string;
  redirectUri: string;
};
