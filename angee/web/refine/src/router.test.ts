// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { tanStackRouterProvider } from "./router";

const routerMock = vi.hoisted(() => ({
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
  useRouterState: () => ({ pathname: "/", search: {} }),
}));

describe("TanStack router provider helpers", () => {
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
