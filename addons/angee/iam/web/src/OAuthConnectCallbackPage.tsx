import { useAuthoredMutation } from "@angee/sdk";
import { useCallback, type ReactNode } from "react";

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
import { connectCallbackRedirectUri } from "./redirects";

const COPY: OAuthCallbackCopy = {
  pendingTitle: "Connecting account...",
  pendingBody: "Your account connection is being confirmed.",
  errorTitle: "Could not connect account",
  backHref: "/iam/providers",
  backLabel: "Back to providers",
  serverError: "The account callback can only be completed in a browser.",
  missingInfo: "The account callback is missing required information.",
  failure: "Could not connect account.",
};

/** OAuth account-connect redirect handler: completes the connect code exchange. */
export function OAuthConnectCallbackPage(): ReactNode {
  const [connectAccountComplete] = useAuthoredMutation<
    ConnectAccountCompleteData,
    ConnectAccountCompleteVariables
  >(CONNECT_ACCOUNT_COMPLETE_MUTATION);

  const complete = useCallback<CallbackExchange>(
    async (args) => {
      const payload = (await connectAccountComplete(args))?.connectAccountComplete;
      if (payload && !payload.error) return { ok: true, next: payload.next };
      return { ok: false, error: payload?.error ?? COPY.failure };
    },
    [connectAccountComplete],
  );

  return (
    <OAuthCallback
      complete={complete}
      copy={COPY}
      fallbackRedirect="/iam/accounts"
      redirectUri={connectCallbackRedirectUri()}
    />
  );
}
