// Non-CRUD console operations the agents pages invoke. Model CRUD is derived from
// the SDL by the DataPage; only bespoke action mutations are authored here.

export const REFRESH_PROVIDER_MODELS_MUTATION = `
  mutation RefreshProviderModels($id: ID!) {
    refreshProviderModels(id: $id) {
      ok
      message
    }
  }
`;

export interface ActionResultData {
  ok: boolean;
  message: string;
}

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

// Record the operator instance the console rendered for an agent (the only
// server-side step of browser-orchestrated provisioning — the daemon calls run
// over the operator connection).
export const PROVISION_AGENT_MUTATION = `
  mutation ProvisionAgent($id: ID!, $workspace: String!, $service: String!) {
    provisionAgent(id: $id, workspace: $workspace, service: $service) {
      ok
      message
    }
  }
`;

export interface ProvisionAgentData {
  provisionAgent: ActionResultData;
}

export interface ProvisionAgentVariables extends Record<string, unknown> {
  id: string;
  workspace: string;
  service: string;
}

// Clear an agent's recorded operator instance after teardown.
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

export interface IdVariables extends Record<string, unknown> {
  id: string;
}
