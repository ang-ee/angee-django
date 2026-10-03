// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { ResourceContext } from "@refinedev/core";
import { describe, expect, test, vi } from "vitest";

import { createTanStackRouterProvider, tanStackRouterProvider } from "./router";

const routerMock = vi.hoisted(() => ({
  location: { pathname: "/records/r1", search: { tab: "details" } },
  buildLocation: vi.fn(
    ({ to, search, hash }: { to: string; search?: unknown; hash?: string }) => ({
      href: `${to}?encoded=${JSON.stringify(search)}${hash ? `#${hash}` : ""}`,
      pathname: to,
      searchStr: "",
      hash: hash ? `#${hash}` : "",
    }),
  ),
  navigate: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => children,
  useNavigate: () => routerMock.navigate,
  useRouter: () => ({ buildLocation: routerMock.buildLocation }),
  useRouterState: () => routerMock.location,
}));

describe("TanStack router provider helpers", () => {
  test("matches a selected preset's pathname while preserving its query-bearing navigation URL", () => {
    routerMock.location.pathname = "/records";
    const resources = [
      { name: "all", list: "/records" },
      { name: "mine", list: "/records?preset=mine" },
    ];
    try {
      const provider = createTanStackRouterProvider("mine");
      const { result } = renderHook(() => provider.parse!(), {
        wrapper: ({ children }: { children: ReactNode }) => createElement(ResourceContext.Provider, { value: { resources } }, children),
      });
      expect(result.current()).toMatchObject({ resource: { name: "mine" }, action: "list" });
      expect(resources[1]?.list).toBe("/records?preset=mine");
    } finally {
      routerMock.location.pathname = "/records/r1";
    }
  });

  test("keeps navigation hooks and Link stable when the menu destination changes", () => {
    const first = createTanStackRouterProvider("first");
    const next = createTanStackRouterProvider("next");
    expect(next.go).toBe(first.go);
    expect(next.back).toBe(first.back);
    expect(next.Link).toBe(first.Link);
  });

  test.each([undefined, "owner", "removed", "elsewhere"])("prefers a matching route owner and otherwise keeps native matching (%s)", (identifier) => {
    const resources = [
      { name: "foreign", list: "/records", show: "/records/:id" },
      { name: "owned", identifier: "owner", list: "/records", show: "/records/:id" },
      { name: "elsewhere", list: "/elsewhere" },
    ];
    const provider = createTanStackRouterProvider(identifier);
    const { result } = renderHook(() => provider.parse!(), {
      wrapper: ({ children }: { children: ReactNode }) => createElement(ResourceContext.Provider, { value: { resources } }, children),
    });
    expect(result.current()).toMatchObject({
      resource: { name: identifier === "owner" ? "owned" : "foreign" },
      action: "show", id: "r1", params: { id: "r1", tab: "details" },
    });
  });

  test("builds refine path requests through the TanStack router", () => {
    const { result } = renderHook(() => tanStackRouterProvider.go!());

    expect(
      result.current({
        type: "path",
        to: "/notes",
        query: { page: 2, empty: "", missing: null },
        hash: "top",
      }),
    ).toBe('/notes?encoded={"page":2,"empty":"","missing":null}#top');
    expect(routerMock.buildLocation).toHaveBeenCalledWith({
      to: "/notes",
      search: { page: 2, empty: "", missing: null },
      hash: "top",
    });
  });
});
