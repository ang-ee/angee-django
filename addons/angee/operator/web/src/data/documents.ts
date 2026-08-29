// Operations against Django's console schema. The operator daemon documents live
// in `documents.daemon.ts` so project console codegen never plucks daemon fields.

import { graphql } from "@angee/gql/console";

// The Django console field that hands the browser the daemon endpoint + a
// short-lived scoped token. This targets Django's console schema, not the daemon.
export const OperatorConnectionQuery = graphql(`
  query OperatorConnection {
    operator_connection {
      endpoint
      token
    }
  }
`);
