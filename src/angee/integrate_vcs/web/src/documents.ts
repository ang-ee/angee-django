import { graphql, type DocumentType } from "@angee/gql/console";

// Resource writes use the native CRUD contracts; authored operations own inventory actions.

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

// Inventory writes create `integrate_vcs.Repository` resource rows; keep that blast
// radius beside the verb that owns it.
export const INTEGRATE_ADD_REPOSITORY_INVALIDATES = ["integrate_vcs.Repository"] as const;

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
