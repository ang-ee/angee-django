import {
  OAuthCallback,
  type CallbackExchange,
  type OAuthCallbackCopy,
} from "@angee/app/auth";
import { useAuthoredMutation } from "@angee/ui";
import { useCallback, useMemo, type ReactNode } from "react";

import { useIntegrateT } from "../i18n";
import { IntegrateConnectAccountComplete } from "./documents.public";
import { currentConnectCallbackRedirectUri } from "./redirects";

/** OAuth account-connect redirect handler: completes the connect code exchange. */
export function OAuthConnectCallbackPage(): ReactNode {
  const t = useIntegrateT();
  const copy = useMemo<OAuthCallbackCopy>(
    () => ({
      pendingTitle: t("integrate.connectCallback.completing"),
      pendingBody: t("integrate.connectCallback.confirming"),
      errorTitle: t("integrate.connectCallback.failedTitle"),
      backHref: "/integrate/providers",
      backLabel: t("integrate.connectCallback.backToProviders"),
      serverError: t("integrate.connectCallback.browserOnly"),
      missingInfo: t("integrate.connectCallback.missingInfo"),
      failure: t("integrate.connectCallback.completeError"),
    }),
    [t],
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
      fallbackRedirect="/integrate/accounts"
      redirectUri={currentConnectCallbackRedirectUri()}
    />
  );
}
