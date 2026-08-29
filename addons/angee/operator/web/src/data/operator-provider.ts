import {
  bearerAuthFromGetter,
  createAngeeHasuraDataProvider,
} from "@angee/refine";

import { operatorToken } from "./operator-token";

/** The fully-resolved refine data provider the factory yields. */
type OperatorDataProvider = ReturnType<typeof createAngeeHasuraDataProvider>;

/** Provider name the operator addon claims via the `dataProviders` seam. */
export const OPERATOR_PROVIDER = "operator";

/**
 * Same-origin browser path to the daemon GraphQL surface. Django proxies it
 * (settings owner: `ANGEE_OPERATOR_GRAPHQL_ENDPOINT`), so queries and mutations
 * ride the console origin while the daemon's self-reported `endpoint` from
 * `operator_connection` is used only by the websocket/log transports that must
 * reach the daemon directly.
 */
const OPERATOR_GRAPHQL_ENDPOINT = "/operator/graphql";

/**
 * The `operator` refine data provider: a Hasura-shaped provider over the daemon
 * GraphQL endpoint, authed by the live bearer in {@link operatorToken}. Built
 * once at app composition; `bearerAuthFromGetter` reads the token per request so
 * a rotation never rebuilds it. The daemon SDL is the default Angee Hasura shape
 * (`idType: "String"`, `hasura-default`), so no provider-option override.
 */
export function createOperatorDataProvider(): OperatorDataProvider {
  return createAngeeHasuraDataProvider({
    url: OPERATOR_GRAPHQL_ENDPOINT,
    auth: bearerAuthFromGetter(operatorToken.get),
  });
}
