import { useAuthoredMutation } from "@angee/refine";
import { Button, ErrorBanner } from "@angee/ui";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { IamRedeemSignInLink } from "./documents.public";
import { useIamT } from "./i18n";
import { IamLoginPage } from "./IamLoginPage";

/** Read the link's token from the URL fragment, which no request, log or referrer carries. */
function readToken(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.hash.slice(1)).get("token") || null;
}

/**
 * The page a sign-in link opens: the login page with the link as its sign-in
 * method, above the usual password form.
 */
export function SignInLinkPage(): ReactNode {
  return <IamLoginPage methods={<SignInLinkMethod />} />;
}

/**
 * Redeem the link only on an explicit click, so a mail scanner that opens it
 * cannot spend it. The token leaves the address bar once read; every refusal
 * reads the same.
 */
export function SignInLinkMethod(): ReactNode {
  const t = useIamT();
  const [token] = useState(readToken);
  const [pending, setPending] = useState(false);
  const [refused, setRefused] = useState(token === null);
  // This method renders its own refusal; no generic notification on top.
  const [redeem] = useAuthoredMutation(IamRedeemSignInLink, { errorNotification: false });

  useEffect(() => {
    if (token !== null) window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }, [token]);

  const signIn = useCallback(async () => {
    if (token === null) return;
    setPending(true);
    try {
      const result = await redeem({ token });
      if (result?.redeem_sign_in_link.ok) {
        // Keep the button disabled while the full-page navigation lands.
        window.location.assign("/");
        return;
      }
    } catch {
      // A transport failure reads like any other refusal.
    }
    setRefused(true);
    setPending(false);
  }, [redeem, token]);

  if (refused) {
    return <ErrorBanner title={t("signInLink.refusedTitle")} description={t("signInLink.refused")} />;
  }
  return (
    <Button className="w-full justify-center" size="lg" disabled={pending} onClick={() => void signIn()}>
      {t("signInLink.continue")}
    </Button>
  );
}
