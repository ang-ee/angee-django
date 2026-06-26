// Public-schema operations for the unauthenticated login surface: the available
// SSO connections and the OAuth login start/complete handshake. These root fields
// live in the `public` runtime schema, so this file is globbed against it by the
// per-schema codegen (the `documents.public.ts` filename is load-bearing — see
// `codegen.shared.ts`). Console/admin operations stay in `./documents`.

import { graphql, type DocumentType } from "@angee/gql/public";

export const IamAvailableConnections = graphql(`
  query IamAvailableConnections {
    available_connections {
      results {
        oauth_client_sqid
        oauth_client_display_name
        oauth_client_slug
        oauth_client_icon
        is_oidc
      }
    }
  }
`);

/** One `available_connections.results` item — a sign-in provider button. */
export type AvailableConnection =
  DocumentType<typeof IamAvailableConnections>["available_connections"]["results"][number];

export const IamLoginStart = graphql(`
  mutation IamLoginStart(
    $oauthClientSqid: String!
    $redirectUri: String!
    $next: String!
  ) {
    login_start(
      oauth_client_sqid: $oauthClientSqid
      redirect_uri: $redirectUri
      next: $next
    ) {
      authorize_url
      error
    }
  }
`);

export const IamLoginComplete = graphql(`
  mutation IamLoginComplete(
    $code: String!
    $state: String!
    $redirectUri: String!
  ) {
    login_complete(code: $code, state: $state, redirect_uri: $redirectUri) {
      ok
      next
      error
    }
  }
`);
