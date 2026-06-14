// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider } from "@angee/sdk";
import { baseIcons } from "@angee/base";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import {
  OAuthCallback,
  type CallbackExchange,
  type OAuthCallbackCopy,
} from "./OAuthCallback";

const COPY: OAuthCallbackCopy = {
  pendingTitle: "Completing...",
  pendingBody: "Hang tight.",
  errorTitle: "Could not finish",
  backHref: "/back",
  backLabel: "Go back",
  serverError: "Browser only.",
  missingInfo: "Missing required information.",
  failure: "It failed.",
};

const REDIRECT_URI = "https://app.example/cb";

beforeAll(() => {
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
});

beforeEach(() => {
  vi.spyOn(window.location, "assign").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/cb");
});

describe("OAuthCallback", () => {
  test("shows the missing-info error and skips the exchange without code/state", () => {
    window.history.replaceState(null, "", "/cb");
    const complete = vi.fn<CallbackExchange>();

    renderCallback(complete);

    expect(screen.getByText(COPY.missingInfo)).toBeTruthy();
    expect(complete).not.toHaveBeenCalled();
  });

  test("surfaces a provider error_description without running the exchange", () => {
    window.history.replaceState(
      null,
      "",
      "/cb?error=access_denied&error_description=User%20declined",
    );
    const complete = vi.fn<CallbackExchange>();

    renderCallback(complete);

    expect(screen.getByText("User declined")).toBeTruthy();
    expect(complete).not.toHaveBeenCalled();
  });

  test("exchanges the code and redirects to a safe next on success", async () => {
    window.history.replaceState(null, "", "/cb?code=ok-code&state=ok-state");
    const complete = vi
      .fn<CallbackExchange>()
      .mockResolvedValue({ ok: true, next: "/iam/accounts" });

    renderCallback(complete);

    await waitFor(() => expect(vi.mocked(window.location.assign)).toHaveBeenCalledWith("/iam/accounts"));
    expect(complete).toHaveBeenCalledWith({
      code: "ok-code",
      state: "ok-state",
      redirectUri: REDIRECT_URI,
    });
  });

  test("falls back to the page's redirect when the outcome carries no next", async () => {
    window.history.replaceState(null, "", "/cb?code=bare-code&state=bare-state");
    const complete = vi
      .fn<CallbackExchange>()
      .mockResolvedValue({ ok: true, next: null });

    renderCallback(complete, { fallbackRedirect: "/home" });

    await waitFor(() => expect(vi.mocked(window.location.assign)).toHaveBeenCalledWith("/home"));
  });

  test("renders the error frame when the exchange returns a failure outcome", async () => {
    window.history.replaceState(null, "", "/cb?code=bad-code&state=bad-state");
    const complete = vi
      .fn<CallbackExchange>()
      .mockResolvedValue({ ok: false, error: "Provider said no." });

    renderCallback(complete);

    expect(await screen.findByText("Provider said no.")).toBeTruthy();
    expect(vi.mocked(window.location.assign)).not.toHaveBeenCalled();
  });

  test("runs the exchange exactly once across two mounts of the same callback", async () => {
    window.history.replaceState(null, "", "/cb?code=once-code&state=once-state");
    let resolve: ((value: { ok: false; error: string }) => void) | undefined;
    const complete = vi.fn<CallbackExchange>().mockImplementation(
      () =>
        new Promise((res) => {
          resolve = res;
        }),
    );

    const first = renderCallback(complete);
    renderCallback(complete);

    expect(complete).toHaveBeenCalledTimes(1);

    resolve?.({ ok: false, error: "done" });
    await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
    first.unmount();
  });
});

function renderCallback(
  complete: CallbackExchange,
  overrides: { fallbackRedirect?: string } = {},
): ReturnType<typeof render> {
  return render(
    <Runtime>
      <OAuthCallback
        complete={complete}
        copy={COPY}
        fallbackRedirect={overrides.fallbackRedirect ?? "/"}
        redirectUri={REDIRECT_URI}
      />
    </Runtime>,
  );
}

function Runtime({ children }: { children: ReactNode }): ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      {children}
    </AppRuntimeProvider>
  );
}
