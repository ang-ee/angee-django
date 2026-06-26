// Bespoke custom operations for the integrate console. Model CRUD is model-driven
// (ResourceList reads the SDL); these are the non-CRUD operations a ResourceList needs that
// aren't single-id `{ ok, message }` actions. Single-id action mutations use
// `useActionMutation(field)` from `@angee/ui` at the call site — no document is
// authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

// The OAuth connect result shared by every `connect_*` mutation that returns a
// `ConnectIntegrationResult` (integrate's `connect_integration`, agents'
// `connect_inference_provider`). One owner for the selection; consumers spread it.
// The client-preset resolves the fragment by name across both addons' `documents.ts`.
export const ConnectOAuthResultFields = graphql(`
  fragment ConnectOAuthResultFields on ConnectIntegrationResult {
    attached
    authorize_url
    error
    mode
    state
    redirect_uri
  }
`);

export const ConnectIntegration = graphql(`
  mutation ConnectIntegration(
    $integrationId: ID!
    $redirectUri: String!
    $next: String!
  ) {
    connect_integration(
      integration_id: $integrationId
      redirect_uri: $redirectUri
      next: $next
    ) {
      ...ConnectOAuthResultFields
    }
  }
`);

export const RotateWebhookSecret = graphql(`
  mutation RotateWebhookSecret($id: ID!) {
    rotate_webhook_secret(id: $id) { ok secret }
  }
`);

// --- VCS console: repo typeahead and inventory actions ---
// VcsBridge/Source CRUD and Repository delete stay model-driven (ResourceList
// reads the SDL). These are the bespoke operations the VCS views need: the repo
// search typeahead and inventory mutations whose variables do not match the
// single-id ActionResult helper.

/** The add typeahead: host repositories matching a typed query, not yet inventoried. */
export const IntegrateSearchRepositories = graphql(`
  query IntegrateSearchRepositories($vcsBridgeId: ID!, $query: String!) {
    search_repositories(vcs_bridge_id: $vcsBridgeId, query: $query) {
      name
      org
      default_branch
      visibility
      web_url
    }
  }
`);

/** Inventory one picked repository; returns the created row. */
export const IntegrateAddRepository = graphql(`
  mutation IntegrateAddRepository($vcsBridgeId: ID!, $name: String!) {
    add_repository(vcs_bridge_id: $vcsBridgeId, name: $name) {
      id
      org
      name
    }
  }
`);

/** Bulk-inventory every repository an account exposes. */
export const IntegrateDiscoverRepositories = graphql(`
  mutation IntegrateDiscoverRepositories($vcsBridgeId: ID!, $org: String!) {
    discover_repositories(vcs_bridge_id: $vcsBridgeId, org: $org) { ok message }
  }
`);

/** One host repository candidate the add typeahead lists (the SDL `RepoCandidate`). */
export type RepoCandidate = DocumentType<
  typeof IntegrateSearchRepositories
>["search_repositories"][number];
