// Bespoke custom operations for the integrate console. Model CRUD is model-driven
// (ResourceList reads the SDL); these are the non-CRUD operations a ResourceList needs that
// aren't single-id `{ ok, message }` actions. Single-id action mutations use
// `useActionMutation(field)` from `@angee/refine` at the call site — no document is
// authored here.

import { graphql, type DocumentType } from "@angee/gql/console";

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

export type IntegrationCapability = DocumentType<
  typeof IntegrationCapabilities
>["integration_capabilities"][number];

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
