// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRouter, type AnyRouter } from "@tanstack/react-router";
import { useConfirm } from "@angee/ui";
import { afterEach, expect, test } from "vitest";

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

test("given a router, a shell page renders under a memory router at its entries, handed to the test", () => {
  const routers: AnyRouter[] = [];
  let seen: AnyRouter | undefined;
  function Page() { seen = useRouter(); return null; }
  render(<ShellPageTestProviders router={{ initialEntries: ["/notes/1"] }} onRouter={(router) => routers.push(router)}><Page /></ShellPageTestProviders>);
  expect(routers).toHaveLength(1);
  expect(seen).toBe(routers[0]);
  expect(routers[0]!.history.location.pathname).toBe("/notes/1");
});
