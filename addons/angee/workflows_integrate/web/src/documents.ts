import { graphql } from "@angee/gql/console";

export const IntegrationSyncRun = graphql(`
  query IntegrationSyncRun($id: String!) {
    integrations_by_pk(id: $id) {
      id
      sync_run
    }
  }
`);
