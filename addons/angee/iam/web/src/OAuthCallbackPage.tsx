import {
  OAuthCallback,
  type CallbackExchange,
  type OAuthCallbackCopy,
} from "@angee/app/auth";
import { useAuthoredMutation } from "@angee/ui";
import { useCallback, useMemo, type ReactNode } from "react";

import { IamLoginComplete } from "./documents.public";
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
