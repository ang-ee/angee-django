// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, baseIcons } from "@angee/ui";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { SignInLinkMethod } from "./SignInLinkPage";

const mocks = vi.hoisted(() => ({ mutate: vi.fn(), proceed: vi.fn() }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.mutate, { fetching: false, error: null }],
}));

vi.mock("@angee/app/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/app/auth")>()),
  useSignInRedirect: () => mocks.proceed,
}));

beforeEach(() => {
  mocks.mutate.mockReset();
  mocks.proceed.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("SignInLinkMethod", () => {
  test("clears the fragment through the router, redeems only on click and names the account", async () => {
    const { history } = renderAt("/sign-in-link#token=usr_abc.secret");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: true, user: { display_name: "Ada Example" } } });

    const button = await screen.findByRole("button", { name: "Sign in with your link" });
    await waitFor(() => expect(history.location.href).toBe("/sign-in-link"));
    expect(mocks.mutate).not.toHaveBeenCalled();
    fireEvent.click(button);

    expect(await screen.findByText("You are signed in as Ada Example.")).toBeTruthy();
    expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith({ token: "usr_abc.secret" });
    expect(mocks.proceed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(mocks.proceed).toHaveBeenCalledOnce();
  });

  test("shows one plain refusal when the link is refused", async () => {
    renderAt("/sign-in-link#token=usr_abc.spent");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: false, user: null } });

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with your link" }));

    expect(await screen.findByText("This sign-in link cannot be used")).toBeTruthy();
    expect(mocks.proceed).not.toHaveBeenCalled();
  });

  test("keeps the link and offers a retry when the server cannot be reached", async () => {
    renderAt("/sign-in-link#token=usr_abc.secret");
    mocks.mutate
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ redeem_sign_in_link: { ok: true, user: { display_name: "Ada Example" } } });

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with your link" }));
    expect(await screen.findByText("Could not reach the server")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("You are signed in as Ada Example.")).toBeTruthy();
    expect(mocks.mutate).toHaveBeenCalledTimes(2);
    expect(mocks.mutate).toHaveBeenLastCalledWith({ token: "usr_abc.secret" });
  });

  test("refuses a visit without a token and calls nothing", async () => {
    renderAt("/sign-in-link");

    expect(await screen.findByText("This sign-in link cannot be used")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in with your link" })).toBeNull();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });
});

function renderAt(url: string) {
  const rootRoute = createRootRoute();
  const page = createRoute({ getParentRoute: () => rootRoute, path: "/sign-in-link", component: SignInLinkMethod });
  const history = createMemoryHistory({ initialEntries: [url] });
  const router = createRouter({ routeTree: rootRoute.addChildren([page]), history });
  render(
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <RouterProvider router={router} />
    </AppRuntimeProvider>,
  );
  return { history };
}
