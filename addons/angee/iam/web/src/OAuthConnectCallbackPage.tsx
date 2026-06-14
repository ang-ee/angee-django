import { useAuthoredMutation } from "@angee/sdk";
import { useCallback, useMemo, type ReactNode } from "react";

import {
  OAuthCallback,
  type CallbackExchange,
  type OAuthCallbackCopy,
} from "./OAuthCallback";
import {
  CONNECT_ACCOUNT_COMPLETE_MUTATION,
  type ConnectAccountCompleteData,
  type ConnectAccountCompleteVariables,
} from "./documents";
import { useIamT } from "./i18n";
import { connectCallbackRedirectUri } from "./redirects";

/** OAuth account-connect redirect handler: completes the connect code exchange. */
export function OAuthConnectCallbackPage(): ReactNode {
  const t = useIamT();
  const copy = useMemo<OAuthCallbackCopy>(
    () => ({
      pendingTitle: t("iam.connectCallback.completing"),
      pendingBody: t("iam.connectCallback.confirming"),
      errorTitle: t("iam.connectCallback.failedTitle"),
      backHref: "/iam/providers",
      backLabel: t("iam.connectCallback.backToProviders"),
      serverError: t("iam.connectCallback.browserOnly"),
      missingInfo: t("iam.connectCallback.missingInfo"),
      failure: t("iam.connectCallback.completeError"),
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
      fallbackRedirect="/iam/accounts"
      redirectUri={connectCallbackRedirectUri()}
    />
  );
}
