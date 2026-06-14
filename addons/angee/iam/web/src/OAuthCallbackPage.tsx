import { useAuthoredMutation } from "@angee/sdk";
import { useCallback, type ReactNode } from "react";

import {
  OAuthCallback,
  type CallbackExchange,
  type OAuthCallbackCopy,
} from "./OAuthCallback";
import {
  LOGIN_COMPLETE_MUTATION,
  type LoginCompleteData,
  type LoginCompleteVariables,
} from "./documents";
import { loginCallbackRedirectUri } from "./redirects";

const COPY: OAuthCallbackCopy = {
  pendingTitle: "Completing sign-in...",
  pendingBody: "Your session is being confirmed.",
  errorTitle: "Could not sign in",
  backHref: "/login",
  backLabel: "Back to sign in",
  serverError: "The sign-in callback can only be completed in a browser.",
  missingInfo: "The sign-in callback is missing required information.",
  failure: "Could not complete sign-in.",
};

/** OIDC sign-in redirect handler: completes the login code exchange. */
export function OAuthCallbackPage(): ReactNode {
  const [loginComplete] = useAuthoredMutation<
    LoginCompleteData,
    LoginCompleteVariables
  >(LOGIN_COMPLETE_MUTATION);

  const complete = useCallback<CallbackExchange>(
    async (args) => {
      const payload = (await loginComplete(args))?.loginComplete;
      if (payload?.ok) return { ok: true, next: payload.next };
      return { ok: false, error: payload?.error ?? COPY.failure };
    },
    [loginComplete],
  );

  return (
    <OAuthCallback
      complete={complete}
      copy={COPY}
      fallbackRedirect="/"
      redirectUri={loginCallbackRedirectUri()}
    />
  );
}
