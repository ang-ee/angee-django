// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, baseIcons } from "@angee/ui";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import { OAuthCallbackPage } from "./OAuthCallbackPage";

const mocks = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return { ...actual };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
}));

beforeAll(() => {
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
});

beforeEach(() => {
  vi.spyOn(window.location, "assign").mockImplementation(() => {});
  mocks.mutate.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/sso/callback");
});

describe("OAuthCallbackPage", () => {
  test("redirects to next when the login payload reports ok", async () => {
    window.history.replaceState(null, "", "/sso/callback?code=login-ok&state=s1");
    mocks.mutate.mockResolvedValue({
      login_complete: { ok: true, next: "/dashboard", error: null },
    });

    render(
      <Runtime>
        <OAuthCallbackPage />
      </Runtime>,
    );

    await waitFor(() => expect(vi.mocked(window.location.assign)).toHaveBeenCalledWith("/dashboard"));
    expect(mocks.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ code: "login-ok", state: "s1" }),
    );
  });

  test("renders the error frame when the login payload is not ok", async () => {
    window.history.replaceState(null, "", "/sso/callback?code=login-bad&state=s2");
    mocks.mutate.mockResolvedValue({
      login_complete: { ok: false, next: "", error: "Account disabled" },
    });

    render(
      <Runtime>
        <OAuthCallbackPage />
      </Runtime>,
    );

    expect(await screen.findByText("Account disabled")).toBeTruthy();
    expect(vi.mocked(window.location.assign)).not.toHaveBeenCalled();
  });
});

function Runtime({ children }: { children: ReactNode }): ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      {children}
    </AppRuntimeProvider>
  );
}
