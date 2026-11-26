// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const mocks = vi.hoisted(() => ({ mutate: vi.fn(), options: vi.fn(), proceed: vi.fn() }));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: (_document: unknown, options: unknown) => {
    mocks.options(options);
    return [mocks.mutate, { fetching: false, error: null }];
  },
}));

vi.mock("@angee/app/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/app/auth")>()),
  useSignInRedirect: () => mocks.proceed,
}));

beforeEach(() => {
  mocks.mutate.mockReset();
  mocks.options.mockReset();
  mocks.proceed.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("SignInLinkMethod", () => {
  test("clears the fragment from history, redeems only on click through a transient mutation and names the account", async () => {
    const { history } = renderAt("/sign-in-link#token=usr_abc.secret");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: true, user: { display_name: "Ada Example" } } });

    const button = await screen.findByRole("button", { name: "Sign in with your link" });
    await waitFor(() => expect(history.location.href).toBe("/sign-in-link"));
    // The token's address was replaced, not pushed: going back cannot reach it.
    expect(history.length).toBe(1);
    expect(mocks.options).toHaveBeenCalledWith(expect.objectContaining({ transient: true, errorNotification: false }));
    expect(mocks.mutate).not.toHaveBeenCalled();
    fireEvent.click(button);

    const signedIn = await screen.findByText("You are signed in as Ada Example.");
    expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith({ token: "usr_abc.secret" });
    await waitFor(() => expect(document.activeElement).toBe(signedIn.closest("[role=status]")));
    expect(mocks.proceed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(mocks.proceed).toHaveBeenCalledOnce();
  });

  test("redeems once however often a pending link is clicked, and says it is busy", async () => {
    renderAt("/sign-in-link#token=usr_abc.secret");
    mocks.mutate.mockReturnValue(new Promise(() => undefined));

    const button = await screen.findByRole("button", { name: "Sign in with your link" });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(button.getAttribute("aria-busy")).toBe("true"));
    expect(mocks.mutate).toHaveBeenCalledOnce();
  });

  test("shows one plain refusal when the link is refused, focused and without the token", async () => {
    renderAt("/sign-in-link#token=usr_abc.zz9");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: false, user: null } });

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with your link" }));

    expect(await screen.findByText("This sign-in link cannot be used")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("alert")));
    expect(document.body.textContent).not.toContain("zz9");
    expect(mocks.proceed).not.toHaveBeenCalled();
  });

  test("keeps the link and offers a retry when the request never reached the server", async () => {
    renderAt("/sign-in-link#token=usr_abc.secret");
    mocks.mutate
      .mockRejectedValueOnce(new Error("Request failed."))
      .mockResolvedValueOnce({ redeem_sign_in_link: { ok: true, user: { display_name: "Ada Example" } } });

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with your link" }));
    expect(await screen.findByText("Could not reach the server")).toBeTruthy();
    expect(document.body.textContent).not.toContain("secret");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("You are signed in as Ada Example.")).toBeTruthy();
    expect(mocks.mutate).toHaveBeenCalledTimes(2);
    expect(mocks.mutate).toHaveBeenLastCalledWith({ token: "usr_abc.secret" });
  });

  test("says the server could not sign in when it answered with an error, not that it was unreachable", async () => {
    renderAt("/sign-in-link#token=usr_abc.secret");
    mocks.mutate.mockRejectedValueOnce(Object.assign(new Error("Request failed."), { response: { status: 500, errors: [] } }));

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with your link" }));

    expect(await screen.findByText("Could not sign in")).toBeTruthy();
    expect(screen.queryByText("Could not reach the server")).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("secret");
  });

  test("a second link opened in the page replaces the first: its token is sent and cleared from every entry", async () => {
    const { history } = renderAt("/sign-in-link#token=usr_abc.first");
    mocks.mutate.mockResolvedValue({ redeem_sign_in_link: { ok: false, user: null } });
    await screen.findByRole("button", { name: "Sign in with your link" });
    await waitFor(() => expect(history.location.href).toBe("/sign-in-link"));

    // The second link changes only the fragment, so the browser pushes it onto this page.
    await act(async () => { history.push("/sign-in-link#token=usr_abc.second"); });
    await waitFor(() => expect(history.location.href).toBe("/sign-in-link"));
    fireEvent.click(screen.getByRole("button", { name: "Sign in with your link" }));

    await screen.findByText("This sign-in link cannot be used");
    expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith({ token: "usr_abc.second" });
    // Two entries, neither holding a token: the push added one and the page replaced it.
    expect(history.length).toBe(2);
    await act(async () => { history.back(); });
    expect(history.location.href).toBe("/sign-in-link");
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
