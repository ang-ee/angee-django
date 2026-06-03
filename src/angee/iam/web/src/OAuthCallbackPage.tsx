import { Alert, Button, Spinner, safeRedirectPath } from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import {
  LOGIN_COMPLETE_MUTATION,
  type LoginCompleteData,
  type LoginCompleteVariables,
} from "./documents";
import { loginCallbackRedirectUri } from "./redirects";

type CallbackParams =
  | { kind: "ready"; code: string; state: string }
  | { kind: "error"; message: string };

type CallbackState =
  | { kind: "pending" }
  | { kind: "error"; message: string };

const completionRequests = new Map<string, Promise<LoginCompleteData | undefined>>();

export function OAuthCallbackPage(): ReactNode {
  const params = useMemo(readCallbackParams, []);
  const [state, setState] = useState<CallbackState>(() =>
    params.kind === "ready"
      ? { kind: "pending" }
      : { kind: "error", message: params.message },
  );
  const [loginComplete] = useAuthoredMutation<
    LoginCompleteData,
    LoginCompleteVariables
  >(LOGIN_COMPLETE_MUTATION);

  useEffect(() => {
    if (params.kind !== "ready") return;

    let mounted = true;
    const redirectUri = loginCallbackRedirectUri();
    const requestKey = `${redirectUri}\n${params.code}\n${params.state}`;
    void loginCompleteOnce(requestKey, () =>
      loginComplete({
        code: params.code,
        state: params.state,
        redirectUri,
      }),
    )
      .then((data) => {
        if (!mounted) return;
        const payload = data?.loginComplete;
        if (payload?.ok) {
          window.location.assign(safeRedirectPath(payload.next) ?? "/");
          return;
        }
        setState({
          kind: "error",
          message: payload?.error ?? "Could not complete sign-in.",
        });
      })
      .catch((caught) => {
        if (!mounted) return;
        setState({
          kind: "error",
          message: errorMessage(caught, "Could not complete sign-in."),
        });
      });

    return () => {
      mounted = false;
    };
  }, [loginComplete, params]);

  if (state.kind === "pending") {
    return (
      <CallbackFrame>
        <div
          aria-live="polite"
          className="flex items-center gap-3"
          role="status"
        >
          <Spinner size="md" tone="brand" />
          <div>
            <h1 className="text-base font-semibold text-fg">Completing sign-in...</h1>
            <p className="mt-1 text-sm text-fg-muted">
              Your session is being confirmed.
            </p>
          </div>
        </div>
      </CallbackFrame>
    );
  }

  return (
    <CallbackFrame>
      <div className="flex flex-col gap-4">
        <Alert intent="danger" title="Could not sign in">
          {state.message}
        </Alert>
        <Button asChild className="w-full justify-center" size="lg" variant="secondary">
          <a href="/login">Back to sign in</a>
        </Button>
      </div>
    </CallbackFrame>
  );
}

function CallbackFrame({ children }: { children: ReactNode }): ReactNode {
  return (
    <main className="grid min-h-screen place-items-center bg-canvas px-4 py-10 text-fg">
      <section className="w-full max-w-md rounded-lg border border-border bg-sheet p-6 shadow-sm">
        {children}
      </section>
    </main>
  );
}

function readCallbackParams(): CallbackParams {
  if (typeof window === "undefined") {
    return {
      kind: "error",
      message: "The sign-in callback can only be completed in a browser.",
    };
  }

  const search = new URLSearchParams(window.location.search);
  const providerError = search.get("error");
  if (providerError) {
    return {
      kind: "error",
      message: search.get("error_description") || providerError,
    };
  }

  const code = search.get("code");
  const state = search.get("state");
  if (!code || !state) {
    return {
      kind: "error",
      message: "The sign-in callback is missing required information.",
    };
  }

  return { kind: "ready", code, state };
}

function loginCompleteOnce(
  key: string,
  run: () => Promise<LoginCompleteData | undefined>,
): Promise<LoginCompleteData | undefined> {
  const existing = completionRequests.get(key);
  if (existing) return existing;

  const request = run().catch((caught) => {
    completionRequests.delete(key);
    throw caught;
  });
  completionRequests.set(key, request);
  return request;
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}
