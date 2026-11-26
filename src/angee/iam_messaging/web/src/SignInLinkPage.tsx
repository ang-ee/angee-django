import { useSignInRedirect } from "@angee/app/auth";
import { IamLoginPage } from "@angee/iam";
import { ServerUnavailableError, useAuthoredMutation } from "@angee/refine";
import { Button, ErrorBanner } from "@angee/ui";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { IamMessagingRedeemSignInLink } from "./documents.public";
import { useIamMessagingT } from "./i18n";

type LinkState =
  | { kind: "ready" }
  | { kind: "pending" }
  | { kind: "unreachable" }
  | { kind: "failed" }
  | { kind: "refused" }
  | { kind: "signed-in"; name: string };

/**
 * Whether a redeem failed before the server answered it: an outage the app's connection found, or a request
 * that never reached the server. Refine's transport error keeps the server's `response` on every failure the
 * server answered.
 */
function unanswered(error: unknown): boolean {
  return error instanceof ServerUnavailableError || !(typeof error === "object" && error !== null && "response" in error);
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
 * cannot spend it. The token is read from the URL fragment, which no request,
 * log or referrer carries, and then dropped from the address bar; a second link
 * opened in the same page takes the first one's place; the transient
 * mutation keeps it out of the client's mutation cache and runs once at a time.
 * A request that never reached the server keeps the link and offers a retry; a
 * server error says so; every refusal reads the same. Once signed in, the page
 * names the account before continuing. Each outcome takes focus, so it is read
 * out where the clicked button was.
 */
export function SignInLinkMethod(): ReactNode {
  const t = useIamMessagingT();
  const navigate = useNavigate();
  const hash = useRouterState({ select: (state) => state.location.hash });
  const linked = new URLSearchParams(hash).get("token") || null;
  const [token, setToken] = useState(linked);
  const [state, setState] = useState<LinkState>(() => (linked === null ? { kind: "refused" } : { kind: "ready" }));
  if (linked !== null && linked !== token && state.kind !== "pending") {
    // A second link opened in this page replaces the held one, unredeemed; the effect below clears it too.
    setToken(linked);
    setState({ kind: "ready" });
  }
  // This method renders its own refusal; no generic notification on top.
  const [redeem] = useAuthoredMutation(IamMessagingRedeemSignInLink, { transient: true, errorNotification: false });
  const proceed = useSignInRedirect();
  const outcome = useRef<HTMLDivElement>(null);
  const settled = useRef(false);

  useEffect(() => {
    // The router keeps a fragment only when asked to; replacing keeps the token out of history.
    if (token !== null) void navigate({ to: ".", search: true, replace: true });
  }, [navigate, token]);

  useEffect(() => {
    if (!settled.current || state.kind === "pending") return;
    settled.current = false;
    outcome.current?.focus();
  }, [state]);

  const signIn = useCallback(async () => {
    if (token === null) return;
    setState({ kind: "pending" });
    let data: Awaited<ReturnType<typeof redeem>>;
    try {
      data = await redeem({ token });
    } catch (error) {
      settled.current = true;
      setState({ kind: unanswered(error) ? "unreachable" : "failed" });
      return;
    }
    const payload = data?.redeem_sign_in_link;
    settled.current = true;
    setState(payload?.ok && payload.user ? { kind: "signed-in", name: payload.user.display_name } : { kind: "refused" });
  }, [redeem, token]);

  if (state.kind === "signed-in") {
    return (
      <div ref={outcome} tabIndex={-1} className="flex flex-col gap-3 outline-none" role="status">
        <p className="text-base font-semibold text-fg">{t("signInLink.signedInAs", { name: state.name })}</p>
        <Button className="w-full justify-center" size="lg" onClick={proceed}>
          {t("signInLink.proceed")}
        </Button>
      </div>
    );
  }
  if (state.kind === "refused") {
    return (
      <ErrorBanner
        ref={outcome}
        tabIndex={-1}
        title={t("signInLink.refusedTitle")}
        description={t("signInLink.refused")}
      />
    );
  }
  const problem = state.kind === "unreachable"
    ? { title: t("signInLink.unreachableTitle"), description: t("signInLink.unreachable") }
    : state.kind === "failed"
      ? { title: t("signInLink.failedTitle"), description: t("signInLink.failed") }
      : null;
  return (
    <div className="flex flex-col gap-3">
      {problem ? (
        <ErrorBanner
          ref={outcome}
          tabIndex={-1}
          title={problem.title}
          description={problem.description}
        />
      ) : null}
      <Button
        className="w-full justify-center"
        size="lg"
        loading={state.kind === "pending"}
        focusableWhenDisabled={state.kind === "pending"}
        onClick={() => void signIn()}
      >
        {t(problem ? "signInLink.retry" : "signInLink.continue")}
      </Button>
    </div>
  );
}
