import { OAuthCallback, type CallbackExchange, type OAuthCallbackCopy, } from "@angee/app/auth";
import { useAuthoredMutation } from "@angee/refine";
import { useCallback, useMemo, type ReactNode } from "react";

import { IamLoginComplete } from "./documents.public";
import { useIamT } from "./i18n";
import { loginCallbackRedirectUri } from "./redirects";

/** OIDC sign-in redirect handler: completes the login code exchange. */
export function OAuthCallbackPage(): ReactNode {
  const t = useIamT();
  const copy = useMemo<OAuthCallbackCopy>(
    () => ({
      pendingTitle: t("callback.completing"),
      pendingBody: t("callback.confirming"),
      errorTitle: t("callback.signInFailed"),
      backHref: "/login",
      backLabel: t("callback.backToSignIn"),
      serverError: t("callback.browserOnly"),
      missingInfo: t("callback.missingInfo"),
      failure: t("callback.completeError"),
    }),
    [t],
  );
  const [loginComplete] = useAuthoredMutation(IamLoginComplete);

  const complete = useCallback<CallbackExchange>(
    async (args) => {
      const payload = (await loginComplete(args))?.login_complete;
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
