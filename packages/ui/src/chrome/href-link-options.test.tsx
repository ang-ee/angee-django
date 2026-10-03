// @vitest-environment happy-dom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test, vi } from "vitest";

import { useHrefLinkOptions } from "./href-link-options";

afterEach(cleanup);

test("chrome href conversion uses the host search parser, route validation, matching and hash", async () => {
  const parseSearch = vi.fn((search: string) => Object.fromEntries(new URLSearchParams(search)));
  const conversions: ReturnType<typeof useHrefLinkOptions>[] = [];
  function ChromeLink() {
    const [, rerender] = useState(0);
    const options = useHrefLinkOptions("/records?preset=all&page=2#details");
    conversions.push(options);
    return <><button onClick={() => rerender((value) => value + 1)}>Rerender</button><Link {...options}>Records</Link></>;
  }
  const root = createRootRoute({ component: ChromeLink });
  const validateSearch = vi.fn((search: Record<string, unknown>) => search);
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/home" }),
      createRoute({ getParentRoute: () => root, path: "/records", validateSearch }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/home"] }), parseSearch,
    stringifySearch: (search) => { const query = new URLSearchParams(search).toString(); return query ? `?${query}` : ""; },
  });
  render(<RouterProvider router={router} />);
  const link = await screen.findByRole("link", { name: "Records" });
  expect(link.getAttribute("href")).toBe("/records?preset=all&page=2#details");
  const options = conversions.at(-1);
  fireEvent.click(screen.getByRole("button", { name: "Rerender" }));
  expect(conversions.at(-1)).toBe(options);
  expect(typeof options?.search).toBe("object");
  expect(fireEvent.click(link)).toBe(false);
  await waitFor(() => expect(router.state.location.pathname).toBe("/records"));
  expect(router.state.location.search).toEqual({ preset: "all", page: "2" });
  expect(router.state.location.hash).toBe("details");
  expect(router.state.matches.at(-1)?.routeId).toBe("/records");
  expect(validateSearch).toHaveBeenCalledWith({ preset: "all", page: "2" });
  await waitFor(() => expect(link.getAttribute("data-status")).toBe("active"));
});

