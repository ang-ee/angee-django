// Bespoke action mutations for the integrate console. Model CRUD is model-driven
// (DataPage reads the SDL); these are the non-CRUD operations a DataPage `<Action>`
// invokes, typed the same way as other authored ops (e.g. iam's grant/revoke).

import type { ActionOutcome, ByIdVariables } from "@angee/sdk";

export const SYNC_INTEGRATION_MUTATION = `
  mutation SyncIntegration($id: ID!) {
    syncIntegration(id: $id) { ok message }
  }
`;

export const TEST_CONNECTION_MUTATION = `
  mutation TestConnection($id: ID!) {
    testConnection(id: $id) { ok message }
  }
`;

export const CONNECT_INTEGRATION_MUTATION = `
  mutation ConnectIntegration($integrationId: ID!, $redirectUri: String!, $next: String!) {
    connectIntegration(integrationId: $integrationId, redirectUri: $redirectUri, next: $next) {
      attached
      authorizeUrl
      error
      mode
      state
      redirectUri
      integration { id status }
    }
  }
`;

/** `{ ok, message }` action outcome — the shared SDK contract. */
export type ActionResultData = ActionOutcome;

export interface SyncIntegrationData {
  syncIntegration: ActionResultData;
}

export interface TestConnectionData {
  testConnection: ActionResultData;
}

export interface ConnectIntegrationData {
  connectIntegration: {
    attached: boolean;
    authorizeUrl: string;
    error: string | null;
    mode?: string;
    state?: string;
    redirectUri?: string;
    integration: { id: string; status: string } | null;
  };
}

export interface ConnectIntegrationVariables extends Record<string, unknown> {
  integrationId: string;
  redirectUri: string;
  next: string;
}

/** Single-id action variables — the shared SDK contract. */
export type IdVariables = ByIdVariables;

export const TEST_WEBHOOK_DELIVERY_MUTATION = `
  mutation TestWebhookDelivery($id: ID!) {
    testWebhookDelivery(id: $id) { ok message }
  }
`;

export const ROTATE_WEBHOOK_SECRET_MUTATION = `
  mutation RotateWebhookSecret($id: ID!) {
    rotateWebhookSecret(id: $id) { ok secret }
  }
`;

export interface TestWebhookDeliveryData {
  testWebhookDelivery: ActionResultData;
}

export interface RotateWebhookSecretData {
  rotateWebhookSecret: { ok: boolean; secret: string };
}

// --- VCS console: bridge picker, repo typeahead, and inventory actions --
// VcsBridge/Source CRUD and Repository delete stay model-driven (DataPage
// reads the SDL). These are the bespoke reads the VCS views need — the
// bridge picker for the add dialog and the repo search typeahead — plus the
// non-CRUD action mutations a button invokes.

/** VCS bridges for the add-repository dialog's bridge picker. */
export const VCS_BRIDGES_QUERY = `
  query IntegrateVcsBridges($pagination: OffsetPaginationInput) {
    vcsIntegrations(pagination: $pagination) {
      results {
        id
        displayName
      }
    }
  }
`;

/** The add typeahead: host repositories matching a typed query, not yet inventoried. */
export const SEARCH_REPOSITORIES_QUERY = `
  query IntegrateSearchRepositories($vcsIntegrationId: ID!, $query: String!) {
    searchRepositories(vcsIntegrationId: $vcsIntegrationId, query: $query) {
      name
      org
      defaultBranch
      visibility
      webUrl
    }
  }
`;

/** Inventory one picked repository; returns the created row. */
export const ADD_REPOSITORY_MUTATION = `
  mutation IntegrateAddRepository($vcsIntegrationId: ID!, $name: String!) {
    addRepository(vcsIntegrationId: $vcsIntegrationId, name: $name) {
      id
      org
      name
    }
  }
`;

/** Bulk-inventory every repository an account exposes. */
export const DISCOVER_REPOSITORIES_MUTATION = `
  mutation IntegrateDiscoverRepositories($vcsIntegrationId: ID!, $org: String!) {
    discoverRepositories(vcsIntegrationId: $vcsIntegrationId, org: $org) { ok message }
  }
`;

/** Refresh every repository's sources for one VCS bridge. */
export const SYNC_VCS_BRIDGE_MUTATION = `
  mutation IntegrateSyncVcsBridge($id: ID!) {
    syncVcsIntegration(id: $id) { ok message }
  }
`;

/** Re-read one source's ref+path from its repository. */
export const REFRESH_SOURCE_MUTATION = `
  mutation IntegrateRefreshSource($id: ID!) {
    refreshSource(id: $id) { ok message }
  }
`;

/** Selection result for one `vcsIntegrations.results` item (the picker option). */
export interface VcsBridgeOption {
  id: string;
  displayName: string;
}

/** Selection result for `IntegrateVcsBridges`. */
export interface VcsBridgesData {
  vcsIntegrations: {
    results: VcsBridgeOption[];
  };
}

export interface VcsBridgesVariables extends Record<string, unknown> {
  pagination?: {
    offset: number;
    limit: number;
  };
}

/** Selection result for one SDL `RepoCandidate` returned by the typeahead. */
export interface RepoCandidate {
  name: string;
  org: string;
  defaultBranch: string;
  visibility: string;
  webUrl: string;
}

/** Selection result for `IntegrateSearchRepositories`. */
export interface SearchRepositoriesData {
  searchRepositories: RepoCandidate[];
}

export interface SearchRepositoriesVariables extends Record<string, unknown> {
  vcsIntegrationId: string;
  query: string;
}

/** Selection result for `IntegrateAddRepository` (the created row). */
export interface AddRepositoryData {
  addRepository: {
    id: string;
    org: string;
    name: string;
  };
}

export interface AddRepositoryVariables extends Record<string, unknown> {
  vcsIntegrationId: string;
  name: string;
}

/** Selection result for `IntegrateDiscoverRepositories`. */
export interface DiscoverRepositoriesData {
  discoverRepositories: ActionResultData;
}

export interface DiscoverRepositoriesVariables extends Record<string, unknown> {
  vcsIntegrationId: string;
  org: string;
}

/** Selection result for `IntegrateSyncVcsBridge`. */
export interface SyncVcsBridgeData {
  syncVcsIntegration: ActionResultData;
}

/** Selection result for `IntegrateRefreshSource`. */
export interface RefreshSourceData {
  refreshSource: ActionResultData;
}
