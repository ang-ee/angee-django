import { useAuthoredMutation } from "@angee/sdk";
import { useCallback, useMemo, type ReactNode } from "react";

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
import { useIamT } from "./i18n";
import { loginCallbackRedirectUri } from "./redirects";

/** OIDC sign-in redirect handler: completes the login code exchange. */
export function OAuthCallbackPage(): ReactNode {
  const t = useIamT();
  const copy = useMemo<OAuthCallbackCopy>(
    () => ({
      pendingTitle: t("iam.callback.completing"),
      pendingBody: t("iam.callback.confirming"),
      errorTitle: t("iam.callback.signInFailed"),
      backHref: "/login",
      backLabel: t("iam.callback.backToSignIn"),
      serverError: t("iam.callback.browserOnly"),
      missingInfo: t("iam.callback.missingInfo"),
      failure: t("iam.callback.completeError"),
    }),
    [t],
  );
  const [loginComplete] = useAuthoredMutation<
    LoginCompleteData,
    LoginCompleteVariables
  >(LOGIN_COMPLETE_MUTATION);

  const complete = useCallback<CallbackExchange>(
    async (args) => {
      const payload = (await loginComplete(args))?.loginComplete;
      if (payload?.ok) return { ok: true, next: payload.next };
      return { ok: false, error: payload?.error ?? copy.failure };
    },
    [loginComplete, copy.failure],
  );

  return (
    <OAuthCallback
      complete={complete}
      copy={copy}
      fallbackRedirect="/"
      redirectUri={loginCallbackRedirectUri()}
    />
  );
}
