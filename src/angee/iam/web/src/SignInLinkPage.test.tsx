// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, baseIcons } from "@angee/ui";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { SignInLinkMethod } from "./SignInLinkPage";

const mocks = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
}));

beforeEach(() => {
  vi.spyOn(window.location, "assign").mockImplementation(() => {});
  mocks.mutate.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/sign-in-link");
});

describe("SignInLinkMethod", () => {
  test("redeems the fragment's token only on click, after clearing it from the address bar", async () => {
    window.history.replaceState(null, "", "/sign-in-link#token=usr_abc.secret");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: true } });

    render(<Runtime><SignInLinkMethod /></Runtime>);

    const button = await screen.findByRole("button", { name: "Sign in with your link" });
    await waitFor(() => expect(window.location.hash).toBe(""));
    expect(mocks.mutate).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(vi.mocked(window.location.assign)).toHaveBeenCalledWith("/"));
    expect(mocks.mutate).toHaveBeenCalledWith({ token: "usr_abc.secret" });
  });

  test("shows one plain refusal when the link is refused", async () => {
    window.history.replaceState(null, "", "/sign-in-link#token=usr_abc.spent");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: false } });

    render(<Runtime><SignInLinkMethod /></Runtime>);

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with your link" }));
    expect(await screen.findByText("This sign-in link cannot be used")).toBeTruthy();
    expect(vi.mocked(window.location.assign)).not.toHaveBeenCalled();
  });

  test("refuses a visit without a token and calls nothing", () => {
    render(<Runtime><SignInLinkMethod /></Runtime>);

    expect(screen.getByText("This sign-in link cannot be used")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in with your link" })).toBeNull();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });
});

function Runtime({ children }: { children: ReactNode }): ReactElement {
  return <AppRuntimeProvider runtime={{ icons: baseIcons }}>{children}</AppRuntimeProvider>;
}
