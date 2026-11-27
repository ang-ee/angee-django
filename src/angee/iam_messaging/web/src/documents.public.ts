// Public-schema operation for the unauthenticated sign-in link page. The root
// field lives in the `public` runtime schema, so this file is globbed against it
// by the per-schema codegen (the `documents.public.ts` filename is load-bearing).
// It is scanned only when this addon is enabled.

import { graphql } from "@angee/gql/public";

export const IamMessagingRedeemSignInLink = graphql(`
  mutation IamMessagingRedeemSignInLink($token: String!) {
    redeem_sign_in_link(token: $token) {
      ok
      user {
        display_name
      }
    }
  }
`);
