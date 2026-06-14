// Non-CRUD console operations the agents pages invoke. Model CRUD is derived from
// the SDL by the DataPage; only bespoke action mutations are authored here.

import type { ActionOutcome, ByIdVariables } from "@angee/sdk";

export const REFRESH_PROVIDER_MODELS_MUTATION = `
  mutation RefreshProviderModels($id: ID!) {
    refreshProviderModels(id: $id) {
      ok
      message
    }
  }
`;

/** `{ ok, message }` action outcome — the shared SDK contract. */
export type ActionResultData = ActionOutcome;

export interface RefreshProviderModelsData {
  refreshProviderModels: ActionResultData;
}

// Re-discover a skill source's skills — the integrate `refreshSource` action,
// invoked from the agents Skills → Sources tab.
export const REFRESH_SOURCE_MUTATION = `
  mutation AgentsRefreshSource($id: ID!) {
    refreshSource(id: $id) {
      ok
      message
    }
  }
`;

export interface RefreshSourceData {
  refreshSource: ActionResultData;
}

// Provision an agent end-to-end, server-side: the Django flow resolves the agent's
// template inputs + credential, syncs the inference secret to the operator, and drives
// the daemon's workspace/service render over its REST API. The console only triggers it.
export const PROVISION_AGENT_MUTATION = `
  mutation ProvisionAgent($id: ID!) {
    provisionAgent(id: $id) {
      ok
      message
    }
  }
`;

export interface ProvisionAgentData {
  provisionAgent: ActionResultData;
}

// Tear down the agent's operator workspace (and its services) and clear the record.
export const DEPROVISION_AGENT_MUTATION = `
  mutation DeprovisionAgent($id: ID!) {
    deprovisionAgent(id: $id) {
      ok
      message
    }
  }
`;

export interface DeprovisionAgentData {
  deprovisionAgent: ActionResultData;
}

/** Single-id action variables — the shared SDK contract. */
export type IdVariables = ByIdVariables;
