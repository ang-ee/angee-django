import { useSignInRedirect } from "@angee/app/auth";
import { IamLoginPage } from "@angee/iam";
import { useAuthoredMutation } from "@angee/refine";
import { Button, ErrorBanner } from "@angee/ui";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { IamMessagingRedeemSignInLink } from "./documents.public";
import { useIamMessagingT } from "./i18n";

type LinkState =
  | { kind: "ready" }
  | { kind: "pending" }
  | { kind: "unreachable" }
  | { kind: "refused" }
  | { kind: "signed-in"; name: string };

/**
 * The page a sign-in link opens: the login page with the link as its sign-in
 * method, above the usual password form.
 */
export function SignInLinkPage(): ReactNode {
  return <IamLoginPage methods={<SignInLinkMethod />} />;
}

/**
 * Redeem the link only on an explicit click, so a mail scanner that opens it
 * cannot spend it. The token is read from the URL fragment, which no request,
 * log or referrer carries, and then dropped from the address bar. A transport
 * failure keeps the link and offers a retry; every refusal reads the same.
 * Once signed in, the page names the account before continuing.
 */
export function SignInLinkMethod(): ReactNode {
  const t = useIamMessagingT();
  const navigate = useNavigate();
  const hash = useRouterState({ select: (state) => state.location.hash });
  const [token] = useState(() => new URLSearchParams(hash).get("token") || null);
  const [state, setState] = useState<LinkState>(() => (token === null ? { kind: "refused" } : { kind: "ready" }));
  // This method renders its own refusal; no generic notification on top.
  const [redeem] = useAuthoredMutation(IamMessagingRedeemSignInLink, { errorNotification: false });
  const proceed = useSignInRedirect();

  useEffect(() => {
    // The router keeps a fragment only when asked to; replacing keeps the token out of history.
    if (token !== null) void navigate({ to: ".", search: true, replace: true });
  }, [navigate, token]);

  const signIn = useCallback(async () => {
    if (token === null) return;
    setState({ kind: "pending" });
    let payload: { ok: boolean; user?: { display_name: string } | null } | undefined;
    try {
      payload = (await redeem({ token }))?.redeem_sign_in_link;
    } catch {
      setState({ kind: "unreachable" });
      return;
    }
    setState(payload?.ok && payload.user ? { kind: "signed-in", name: payload.user.display_name } : { kind: "refused" });
  }, [redeem, token]);

  if (state.kind === "signed-in") {
    return (
      <div className="flex flex-col gap-3" role="status">
        <p className="text-base font-semibold text-fg">{t("signInLink.signedInAs", { name: state.name })}</p>
        <Button className="w-full justify-center" size="lg" onClick={proceed}>
          {t("signInLink.proceed")}
        </Button>
      </div>
    );
  }
  if (state.kind === "refused") {
    return <ErrorBanner title={t("signInLink.refusedTitle")} description={t("signInLink.refused")} />;
  }
  return (
    <div className="flex flex-col gap-3">
      {state.kind === "unreachable" ? (
        <ErrorBanner title={t("signInLink.unreachableTitle")} description={t("signInLink.unreachable")} />
      ) : null}
      <Button
        className="w-full justify-center"
        size="lg"
        disabled={state.kind === "pending"}
        onClick={() => void signIn()}
      >
        {t(state.kind === "unreachable" ? "signInLink.retry" : "signInLink.continue")}
      </Button>
    </div>
  );
}
