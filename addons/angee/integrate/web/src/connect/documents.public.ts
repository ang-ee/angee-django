// Bespoke account-connect mutations for the outbound connect-for-API surface
// (OAuth account connect). Model CRUD for OAuthClient/ExternalAccount/Credential
// is model-driven (DataPage reads the SDL); these are the non-CRUD operations the
// connect views invoke. The connect start/complete flow is served by the public
// schema (it runs from public-facing pages); the console-only credential reveal
// lives in `documents.console.ts`. Inbound OIDC login
// (loginStart/availableConnections) lives in `@angee/iam`.

import { graphql } from "@angee/gql/public";

export const IntegrateConnectAccountStart = graphql(`
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
`);

export const IntegrateConnectAccountComplete = graphql(`
  mutation IntegrateConnectAccountComplete(
    $code: String!
    $state: String!
    $redirectUri: String!
  ) {
    connectAccountComplete(code: $code, state: $state, redirectUri: $redirectUri) {
      next
      error
      account { id displayName }
      credential { id displayName status }
    }
  }
`);
