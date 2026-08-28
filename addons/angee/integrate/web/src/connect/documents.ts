// Console-only credential mutations: minting a provider-less credential and
// revealing a stored one's secret. The account-connect flow (start/complete) is
// served by the public schema and lives in `documents.public.ts`; these are
// console admin actions, so they target the console schema.

import { graphql } from "@angee/gql/console";

export const IntegrateRevealCredential = graphql(`
  mutation IntegrateRevealCredential($id: ID!) {
    reveal_credential(id: $id) {
      secret
    }
  }
`);

// Credentials expose no auto-CRUD insert root: creating one dispatches the typed
// secret into encrypted material by `kind`, which a column-writing insert cannot
// do. `create_credential` is that owner, so the create form saves through it.
export const IntegrateCreateCredential = graphql(`
  mutation IntegrateCreateCredential($data: CredentialInput!) {
    create_credential(data: $data) {
      id
      display_name
      kind
      status
    }
  }
`);

// Creating a credential adds an `integrate.Credential` row; keep that blast
// radius beside the verb that owns it.
export const INTEGRATE_CREATE_CREDENTIAL_INVALIDATES = ["integrate.Credential"] as const;
