// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, baseIcons } from "@angee/ui";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import { OAuthConnectCallbackPage } from "./OAuthConnectCallbackPage";
import { CONNECT_CALLBACK_PATH } from "./redirects";

const mocks = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
  };
});

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
  window.history.replaceState(null, "", "/integrate/oauth/callback");
});

describe("OAuthConnectCallbackPage", () => {
  test("treats a payload without an error as success and redirects to next", async () => {
    window.history.replaceState(null, "", "/integrate/oauth/callback?code=connect-ok&state=s1");
    mocks.mutate.mockResolvedValue({
      connect_account_complete: {
        next: "/integrate/accounts",
        error: null,
        account: null,
        credential: null,
      },
    });

    render(
      <Runtime>
        <OAuthConnectCallbackPage />
      </Runtime>,
    );

    await waitFor(() => expect(vi.mocked(window.location.assign)).toHaveBeenCalledWith("/integrate/accounts"));
    expect(mocks.mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        code: "connect-ok",
        state: "s1",
        redirectUri: `${window.location.origin}${CONNECT_CALLBACK_PATH}`,
      }),
    );
  });

  test("renders the provider error message when the payload carries an error", async () => {
    window.history.replaceState(null, "", "/integrate/oauth/callback?code=connect-bad&state=s2");
    mocks.mutate.mockResolvedValue({
      connect_account_complete: {
        next: "",
        error: "Rate limited",
        account: null,
        credential: null,
      },
    });

    render(
      <Runtime>
        <OAuthConnectCallbackPage />
      </Runtime>,
    );

    expect(await screen.findByText("Rate limited")).toBeTruthy();
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
