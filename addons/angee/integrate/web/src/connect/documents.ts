// Console-only connect mutation: revealing a stored credential's secret. The
// connect account-connect flow (start/complete) is served by the public schema
// and lives in `documents.public.ts`; this reveal is a console admin action, so
// it targets the console schema.

import { graphql } from "@angee/gql/console";

export const IntegrateRevealCredential = graphql(`
  mutation IntegrateRevealCredential($id: ID!) {
    reveal_credential(id: $id) {
      secret
    }
  }
`);
