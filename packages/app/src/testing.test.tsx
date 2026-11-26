// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter, useRouter, type AnyRouter } from "@tanstack/react-router";
import { useConfirm } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";

import { ShellPageTestProviders } from "./testing";

afterEach(cleanup);

test("a shell page's test providers answer the confirmations a page asks, as the app's ModalsHost does", async () => {
  let answer: Promise<boolean> | undefined;
  function Page() {
    const confirm = useConfirm();
    return <button type="button" onClick={() => { answer = confirm({ title: "Leave the page?", confirm: "Leave" }); }}>Ask</button>;
  }
  render(<ShellPageTestProviders><Page /></ShellPageTestProviders>);
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  fireEvent.click(await screen.findByRole("button", { name: "Leave" }));
  await act(async () => { expect(await answer).toBe(true); });
});

test("a shell page renders under a memory router by default, as every shell page renders under the app's router", () => {
  const routers: AnyRouter[] = [];
  let seen: AnyRouter | undefined;
  function Page() { seen = useRouter(); return null; }
  render(<ShellPageTestProviders onRouter={(router) => routers.push(router)}><Page /></ShellPageTestProviders>);
  expect(seen).toBe(routers[0]);
  expect(routers[0]!.history.location.pathname).toBe("/");
});

test("under a router the test brings, a shell page renders under that one; entries asked for inside it are refused", () => {
  const own = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/own"] }) });
  const routers: AnyRouter[] = [];
  let seen: AnyRouter | undefined;
  function Page() { seen = useRouter(); return null; }
  render(<RouterContextProvider router={own}>
    <ShellPageTestProviders onRouter={(router) => routers.push(router)}><Page /></ShellPageTestProviders>
  </RouterContextProvider>);
  expect(seen).toBe(own);
  expect(routers).toEqual([]);
  cleanup();
  const spied = vi.spyOn(console, "error").mockImplementation(() => undefined);
  try {
    expect(() => render(<RouterContextProvider router={own}>
      <ShellPageTestProviders router={{ initialEntries: ["/elsewhere"] }}><Page /></ShellPageTestProviders>
    </RouterContextProvider>)).toThrow("ShellPageTestProviders was given initialEntries inside a router of the test's own: place that router instead.");
  } finally {
    spied.mockRestore();
  }
});

test("given a router, a shell page renders under a memory router at its entries, handed to the test", () => {
  const routers: AnyRouter[] = [];
  let seen: AnyRouter | undefined;
  function Page() { seen = useRouter(); return null; }
  render(<ShellPageTestProviders router={{ initialEntries: ["/notes/1"] }} onRouter={(router) => routers.push(router)}><Page /></ShellPageTestProviders>);
  expect(routers).toHaveLength(1);
  expect(seen).toBe(routers[0]);
  expect(routers[0]!.history.location.pathname).toBe("/notes/1");
});
