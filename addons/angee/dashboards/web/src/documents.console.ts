import { graphql } from "@angee/gql/console";

export const DashboardDocument = graphql(`
  query Dashboard($target: DashboardTargetInput!) {
    dashboard(target: $target) {
      status id revision name description snapshot
      can_edit can_reset can_archive current_revision message
    }
  }
`);

export const DashboardSummariesDocument = graphql(`
  query DashboardSummaries($cursor: String, $version: String) {
    dashboard_summaries(cursor: $cursor, version: $version, limit: 100) {
      status version next_cursor total
      items {
        id scope scope_key name description owner owner_label revision is_archived
        resources can_edit can_archive
      }
    }
  }
`);

export const CreatePersonalDashboardDocument = graphql(`
  mutation CreatePersonalDashboard($name: String!, $description: String!, $clientCreationKey: String!) {
    create_personal_dashboard(name: $name, description: $description, client_creation_key: $clientCreationKey) {
      status id revision name description snapshot
      can_edit can_reset can_archive current_revision message
    }
  }
`);

export const SaveDashboardDocument = graphql(`
  mutation SaveDashboard(
    $target: DashboardTargetInput!, $snapshot: JSON!, $persistedId: ID,
    $expectedRevision: Int, $declarationRevision: String!, $name: String!, $description: String!
  ) {
    save_dashboard(
      target: $target, snapshot: $snapshot, persisted_id: $persistedId,
      expected_revision: $expectedRevision, declaration_revision: $declarationRevision,
      name: $name, description: $description
    ) {
      status id revision name description snapshot
      can_edit can_reset can_archive current_revision message
    }
  }
`);

export const ResetDashboardDocument = graphql(`
  mutation ResetDashboard($target: DashboardTargetInput!, $persistedId: ID!, $expectedRevision: Int!) {
    reset_dashboard(target: $target, persisted_id: $persistedId, expected_revision: $expectedRevision) {
      status current_revision message
    }
  }
`);

export const DuplicateDashboardDocument = graphql(`
  mutation DuplicateDashboard($target: DashboardTargetInput!, $name: String!, $clientCreationKey: String!) {
    duplicate_dashboard(target: $target, name: $name, client_creation_key: $clientCreationKey) {
      status id revision name description snapshot
      can_edit can_reset can_archive current_revision message
    }
  }
`);

export const ArchiveDashboardDocument = graphql(`
  mutation ArchiveDashboard($id: ID!, $expectedRevision: Int!, $archived: Boolean!) {
    set_personal_dashboard_archived(id: $id, expected_revision: $expectedRevision, archived: $archived) {
      status id revision name description snapshot
      can_edit can_reset can_archive current_revision message
    }
  }
`);
