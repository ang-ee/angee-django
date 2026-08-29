import { OAuthCallback, type CallbackExchange, type OAuthCallbackCopy, } from "@angee/app/auth";
import { useAuthoredMutation } from "@angee/refine";
import { useRouteHref } from "@angee/ui";
import { useCallback, useMemo, type ReactNode } from "react";

import { useIntegrateT } from "../i18n";
import { IntegrateConnectAccountComplete } from "./documents.public";
import { currentConnectCallbackRedirectUri } from "./redirects";

/** OAuth account-connect redirect handler: completes the connect code exchange. */
export function OAuthConnectCallbackPage(): ReactNode {
  const t = useIntegrateT();
  const routeHref = useRouteHref();
  const copy = useMemo<OAuthCallbackCopy>(
    () => ({
      pendingTitle: t("connectCallback.completing"),
      pendingBody: t("connectCallback.confirming"),
      errorTitle: t("connectCallback.failedTitle"),
      backHref: routeHref("integrate.providers"),
      backLabel: t("connectCallback.backToProviders"),
      serverError: t("connectCallback.browserOnly"),
      missingInfo: t("connectCallback.missingInfo"),
      failure: t("connectCallback.completeError"),
    }),
    [routeHref, t],
  );
  const [connectAccountComplete] = useAuthoredMutation(
    IntegrateConnectAccountComplete,
  );

  const complete = useCallback<CallbackExchange>(
    async (args) => {
      const payload = (await connectAccountComplete(args))?.connect_account_complete;
      if (payload && !payload.error) return { ok: true, next: payload.next };
      return { ok: false, error: payload?.error ?? copy.failure };
    },
    [connectAccountComplete, copy.failure],
  );

  return (
    <OAuthCallback
      complete={complete}
      copy={copy}
      fallbackRedirect={routeHref("integrate.accounts")}
      redirectUri={currentConnectCallbackRedirectUri()}
    />
  );
}
