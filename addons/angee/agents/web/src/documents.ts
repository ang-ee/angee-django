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

export interface IdVariables extends Record<string, unknown> {
  id: string;
}
