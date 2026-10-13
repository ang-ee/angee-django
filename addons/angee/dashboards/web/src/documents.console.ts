import { graphql } from "@angee/gql/console";

export const DashboardWidgetVisibilityDocument = graphql(`
  query DashboardWidgetVisibility($policies: [DashboardWidgetVisibilityInput!]!) {
    dashboard_widget_visibility(policies: $policies)
  }
`);

export const DashboardDocument = graphql(`
  query Dashboard($target: DashboardTargetInput!) {
    dashboard(target: $target) {
      status id revision name description snapshot declaration_revision
      can_edit can_reset can_trash current_revision message
    }
  }
`);

export const DashboardSummariesDocument = graphql(`
  query DashboardSummaries($cursor: String, $version: String) {
    dashboard_summaries(cursor: $cursor, version: $version, limit: 100) {
      status version next_cursor total
      items {
        id scope scope_key name description owner owner_label revision is_trashed
        resources presets can_edit can_trash
      }
    }
  }
`);

export const CreatePersonalDashboardDocument = graphql(`
  mutation CreatePersonalDashboard($name: String!, $description: String!, $clientCreationKey: String!) {
    create_personal_dashboard(name: $name, description: $description, client_creation_key: $clientCreationKey) {
      status id revision name description snapshot
      can_edit can_reset can_trash current_revision message
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
      can_edit can_reset can_trash current_revision message
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
  mutation DuplicateDashboard($target: DashboardTargetInput!, $name: String!, $clientCreationKey: String!, $snapshot: JSON!) {
    duplicate_dashboard(target: $target, name: $name, client_creation_key: $clientCreationKey, snapshot: $snapshot) {
      status id revision name description snapshot
      can_edit can_reset can_trash current_revision message
    }
  }
`);

export const TrashDashboardDocument = graphql(`
  mutation TrashDashboard($id: ID!, $expectedRevision: Int!, $trashed: Boolean!) {
    set_personal_dashboard_trashed(id: $id, expected_revision: $expectedRevision, trashed: $trashed) {
      status id revision name description snapshot
      can_edit can_reset can_trash current_revision message
    }
  }
`);
