// Bespoke account-connect mutations for the outbound connect-for-API surface
// (OAuth account connect). Model CRUD for OAuthClient/ExternalAccount/Credential
// is model-driven (ResourceList reads the SDL); these are the non-CRUD operations the
// connect views invoke. The connect start/complete flow is served by the public
// schema (it runs from public-facing pages); the console-only credential reveal
// lives in `documents.console.ts`. Inbound OIDC login
// (`login_start`/`available_connections`) lives in `@angee/iam`.

import { graphql } from "@angee/gql/public";

export const IntegrateConnectAccountStart = graphql(`
  mutation IntegrateConnectAccountStart(
    $id: ID!
    $redirectUri: String!
    $next: String!
  ) {
    connect_account_start(
      id: $id
      redirect_uri: $redirectUri
      next: $next
    ) {
      authorize_url
      error
      mode
      state
      redirect_uri
    }
  }
`);

export const IntegrateConnectAccountComplete = graphql(`
  mutation IntegrateConnectAccountComplete(
    $code: String!
    $state: String!
    $redirectUri: String!
  ) {
    connect_account_complete(code: $code, state: $state, redirect_uri: $redirectUri) {
      next
      error
      account { id display_name }
      credential { id display_name status }
    }
  }
`);
