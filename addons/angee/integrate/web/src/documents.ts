// Authored reads and bespoke result shapes for the integrate console.
// ActionResult mutations use generated action documents through the shared hooks.

import { graphql } from "@angee/gql/console";

export const IntegrationRecordRedirectDocument = graphql(`
  query IntegrationRecordRedirect($id: String!) {
    integrations_by_pk(id: $id) {
      id
      concrete_target { state resource id }
    }
  }
`);

export const IntegrationCapabilities = graphql(`
  query IntegrationCapabilities {
    integration_capabilities {
      resource
      label
      icon
      create_mode
    }
  }
`);

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
    $resource: String!
    $id: ID!
    $redirectUri: String!
    $next: String!
  ) {
    connect_integration(
      resource: $resource
      id: $id
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

/** Route search keeps identity only; its label comes from the retained stream. */
export const IntegrationSyncStream = graphql(`
  query IntegrationSyncStream($id: String!, $includeFailures: Boolean!) {
    sync_streams_by_pk(id: $id) {
      id
      key
      partition
      failures @include(if: $includeFailures) {
        id stream_id link_id slot kind code status is_open attempts retry_at created_at
      }
    }
  }
`);

/** Remote identities attached to one readable record. */
export const RecordSourcesDocument = graphql(`
  query IntegrateRecordSources($modelLabel: String!, $recordId: String!) {
    record_links(
      where: { model_label: { _eq: $modelLabel }, record_id: { _eq: $recordId } }
      order_by: [{ updated_at: desc }]
      limit: 100
    ) {
      id
      external_key
      status
      source_url
      last_verified_generation
      last_seen_at
      applied_at
      stream {
        id
        key
        partition
        integration { id display_name }
      }
    }
  }
`);
