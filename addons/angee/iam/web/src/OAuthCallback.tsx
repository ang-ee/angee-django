import { Alert, Button, Spinner, safeRedirectPath } from "@angee/base";
import { useEffect, useMemo, useState, type ReactNode } from "react";

/**
 * Outcome a flow derives from its completion payload: `ok` redirects to `next`
 * (or the page's fallback), otherwise `error` is shown in the error frame. Each
 * flow maps its own payload here — sign-in keys success on `ok`, account-connect
 * on the absence of an `error` — so the shared machinery never re-decides it.
 */
export type CallbackOutcome =
  | { ok: true; next?: string | null }
  | { ok: false; error: string };

/** The single-use code exchange one flow performs against its redirect_uri. */
export type CallbackExchange = (args: {
  code: string;
  state: string;
  redirectUri: string;
}) => Promise<CallbackOutcome>;

/** User-facing copy for one callback flow (sign-in vs account connect). */
export interface OAuthCallbackCopy {
  pendingTitle: string;
  pendingBody: string;
  errorTitle: string;
  backHref: string;
  backLabel: string;
  /** Shown when the callback runs without a browser (SSR). */
  serverError: string;
  /** Shown when `code`/`state` are absent from the URL. */
  missingInfo: string;
  /** Fallback when the exchange rejects without a usable message. */
  failure: string;
}

export interface OAuthCallbackProps {
  /** The redirect_uri the authorize step was issued with. */
  redirectUri: string;
  /** Where to land on success when the payload carries no `next`. */
  fallbackRedirect: string;
  /** Exchange the authorization code for this flow's outcome. */
  complete: CallbackExchange;
  copy: OAuthCallbackCopy;
}

type CallbackParams =
  | { kind: "ready"; code: string; state: string }
  | { kind: "error"; message: string };

type CallbackState =
  | { kind: "pending" }
  | { kind: "error"; message: string };

// Module-level so a StrictMode double-mount (or a fast remount) reuses the
// in-flight exchange instead of redeeming the single-use code twice. Keyed by
// redirect_uri + code + state, so the two flows never collide.
const completionRequests = new Map<string, Promise<CallbackOutcome>>();

/**
 * Shared OAuth/OIDC redirect handler: reads `code`/`state` (or a provider
 * `error`) from the URL, exchanges the code exactly once, then redirects on
 * success or renders the error frame. Both the sign-in and account-connect
 * callbacks render this with their own `complete` and `copy`.
 */
export function OAuthCallback({
  redirectUri,
  fallbackRedirect,
  complete,
  copy,
}: OAuthCallbackProps): ReactNode {
  const params = useMemo(() => readCallbackParams(copy), [copy]);
  const [state, setState] = useState<CallbackState>(() =>
    params.kind === "ready"
      ? { kind: "pending" }
      : { kind: "error", message: params.message },
  );

  useEffect(() => {
    if (params.kind !== "ready") return;

    let mounted = true;
    const requestKey = `${redirectUri}\n${params.code}\n${params.state}`;
    void completeOnce(requestKey, () =>
      complete({ code: params.code, state: params.state, redirectUri }),
    )
      .then((outcome) => {
        if (!mounted) return;
        if (outcome.ok) {
          window.location.assign(safeRedirectPath(outcome.next) ?? fallbackRedirect);
          return;
        }
        setState({ kind: "error", message: outcome.error });
      })
      .catch((caught) => {
        if (!mounted) return;
        setState({ kind: "error", message: errorMessage(caught, copy.failure) });
      });

    return () => {
      mounted = false;
    };
  }, [complete, params, redirectUri, fallbackRedirect, copy.failure]);

  if (state.kind === "pending") {
    return (
      <CallbackFrame>
        <div className="flex items-center gap-3">
          <Spinner size="md" tone="brand" />
          <div>
            <h1 className="text-base font-semibold text-fg">{copy.pendingTitle}</h1>
            <p
              aria-live="polite"
              className="mt-1 text-sm text-fg-muted"
              role="status"
            >
              {copy.pendingBody}
            </p>
          </div>
        </div>
      </CallbackFrame>
    );
  }

  return (
    <CallbackFrame>
      <div className="flex flex-col gap-4">
        <Alert tone="danger" title={copy.errorTitle}>
          {state.message}
        </Alert>
        <Button asChild className="w-full justify-center" size="lg" variant="secondary">
          <a href={copy.backHref}>{copy.backLabel}</a>
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

function readCallbackParams(copy: OAuthCallbackCopy): CallbackParams {
  if (typeof window === "undefined") {
    return { kind: "error", message: copy.serverError };
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
    return { kind: "error", message: copy.missingInfo };
  }

  return { kind: "ready", code, state };
}

function completeOnce(
  key: string,
  run: () => Promise<CallbackOutcome>,
): Promise<CallbackOutcome> {
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
