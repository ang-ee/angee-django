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
 * The `operator` refine data provider: a Hasura-shaped provider over the daemon
 * GraphQL endpoint, authed by the live bearer in {@link operatorToken}. Built
 * once at app composition; `bearerAuthFromGetter` reads the token per request so
 * a rotation never rebuilds it. The daemon SDL is the default Angee Hasura shape
 * (`idType: "String"`, `hasura-default`), so no provider-option override. The
 * daemon writes its errors for the stack's operators (what failed, on which
 * workspace, slot or job, and the remedy), so every message reaches the page.
 */
export function createOperatorDataProvider(): OperatorDataProvider {
  return createAngeeHasuraDataProvider({
    url: operatorToken.endpoint,
    auth: bearerAuthFromGetter(operatorToken.get),
    errorMessages: "all",
  });
}
