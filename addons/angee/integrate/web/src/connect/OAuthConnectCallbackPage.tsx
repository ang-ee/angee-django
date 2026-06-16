import {
  OAuthCallback,
  type CallbackExchange,
  type OAuthCallbackCopy,
} from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";
import { useCallback, useMemo, type ReactNode } from "react";

import { useIntegrateT } from "../i18n";
import {
  CONNECT_ACCOUNT_COMPLETE_MUTATION,
  type ConnectAccountCompleteData,
  type ConnectAccountCompleteVariables,
} from "./documents";
import { connectCallbackRedirectUri } from "./redirects";

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
  const [connectAccountComplete] = useAuthoredMutation<
    ConnectAccountCompleteData,
    ConnectAccountCompleteVariables
  >(CONNECT_ACCOUNT_COMPLETE_MUTATION);

  const complete = useCallback<CallbackExchange>(
    async (args) => {
      const payload = (await connectAccountComplete(args))?.connectAccountComplete;
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
      redirectUri={connectCallbackRedirectUri()}
    />
  );
}
