// @vitest-environment happy-dom

import type { AuthProvider } from "@refinedev/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { describe, expect, test, vi } from "vitest";

import { authBeforeLoad, authRouteError } from "./route-tree";

function gate(getIdentity: () => Promise<unknown>) {
  const authProvider = { getIdentity } as AuthProvider;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return authBeforeLoad(authProvider, queryClient, "/login");
}

describe("authenticated route identity gate", () => {
  test("preserves a transient identity failure for the route error boundary", async () => {
    const unavailable = new Error("Request failed with status 502");
    await expect(gate(async () => { throw unavailable; })({ location: { href: "/workflows/one" } }))
      .rejects.toMatchObject({ name: "AuthIdentityCheckError" });
  });

  test("retries a transient check without losing the requested route", async () => {
    const getIdentity = vi.fn()
      .mockRejectedValueOnce(new Error("Request failed with status 502"))
      .mockResolvedValueOnce({ id: "user-1" });
    const beforeLoad = gate(getIdentity);
    await expect(beforeLoad({ location: { href: "/workflows/one" } })).rejects.toMatchObject({
      name: "AuthIdentityCheckError",
    });
    await expect(beforeLoad({ location: { href: "/workflows/one" } })).resolves.toBeUndefined();
  });

  test("retries a transient identity failure through the router", async () => {
    const getIdentity = vi.fn()
      .mockRejectedValueOnce(new Error("sensitive upstream 502 detail"))
      .mockResolvedValueOnce({ id: "user-1" });
    const authProvider = { getIdentity } as unknown as AuthProvider;
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const rootRoute = createRootRoute({ component: Outlet });
    const authenticatedRoute = createRoute({
      getParentRoute: () => rootRoute,
      id: "authenticated",
      beforeLoad: authBeforeLoad(authProvider, queryClient, "/login"),
      errorComponent: authRouteError(queryClient, authProvider),
      component: Outlet,
    });
    const requestedRoute = createRoute({
      getParentRoute: () => authenticatedRoute,
      path: "/workflows/one",
      component: () => <p>Requested workflow</p>,
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([
        authenticatedRoute.addChildren([requestedRoute]),
      ]),
      history: createMemoryHistory({ initialEntries: ["/workflows/one"] }),
    });

    render(<RouterProvider router={router} />);

    expect(await screen.findByText("Unable to check your session")).toBeTruthy();
    expect(screen.queryByText("sensitive upstream 502 detail")).toBeNull();
    expect(router.state.location.href).toBe("/workflows/one");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Requested workflow")).toBeTruthy();
    expect(router.state.location.href).toBe("/workflows/one");
    expect(getIdentity).toHaveBeenCalledTimes(2);
  });

  test.each([
    ["absent identity", async () => null],
    ["authoritative 401", async () => { throw { status: 401 }; }],
  ])("redirects only for %s", async (_label, getIdentity) => {
    await expect(gate(getIdentity)({ location: { href: "/workflows/one" } }))
      .rejects.toMatchObject({ options: { to: "/login", search: { next: "/workflows/one" } } });
  });

  test("admits a valid identity and reuses its authenticated cache", async () => {
    const getIdentity = vi.fn(async () => ({ id: "user-1" }));
    const beforeLoad = gate(getIdentity);
    await beforeLoad({ location: { href: "/workflows/one" } });
    await beforeLoad({ location: { href: "/workflows/two" } });
    expect(getIdentity).toHaveBeenCalledOnce();
  });
});
