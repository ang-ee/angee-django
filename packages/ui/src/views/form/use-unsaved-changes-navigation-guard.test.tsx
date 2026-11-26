// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { ModalsHost } from "../../feedback";
import { pathnameChanges, useUnsavedChangesNavigationGuard, type UnsavedChangesMove } from "./use-unsaved-changes-navigation-guard";

afterEach(cleanup);

function DirtyForm({ leaves }: { leaves?: (move: UnsavedChangesMove) => boolean }): ReactNode {
  useUnsavedChangesNavigationGuard({ isDirty: true, isDirtyNow: () => true, readOnly: false, leaves });
  return null;
}

function renderGuard(leaves?: (move: UnsavedChangesMove) => boolean) {
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/threads?thread=one"] }) });
  render(<RouterContextProvider router={router}><ModalsHost><DirtyForm leaves={leaves} /></ModalsHost></RouterContextProvider>);
  return router;
}

/** Start a move and let the guard decide; resolves once the move is decided. */
function move(router: ReturnType<typeof renderGuard>, href: string): Promise<void> {
  let done!: Promise<void>;
  act(() => { done = router.history.push(href) as unknown as Promise<void>; });
  return Promise.resolve(done);
}

describe("useUnsavedChangesNavigationGuard", () => {
  test("a move its predicate does not call leaving raises no prompt and moves", async () => {
    const router = renderGuard(pathnameChanges);
    await move(router, "/threads?thread=two");
    await waitFor(() => expect(router.history.location.search).toBe("?thread=two"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  test("a move its predicate calls leaving asks first: Stay keeps the person where they are", async () => {
    const router = renderGuard(pathnameChanges);
    void move(router, "/elsewhere");
    expect(await screen.findByRole("alertdialog", { name: "Unsaved changes - leave without saving?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stay" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(router.history.location.pathname).toBe("/threads");
  });

  test("without a predicate every move leaves, a search move too", async () => {
    const router = renderGuard();
    void move(router, "/threads?thread=two");
    expect(await screen.findByRole("alertdialog", { name: "Unsaved changes - leave without saving?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(router.history.location.search).toBe("?thread=two"));
  });

  test("pathnameChanges compares the pathnames alone", () => {
    const at = (pathname: string, search: Record<string, unknown> = {}) => ({ pathname, search }) as UnsavedChangesMove["current"];
    expect(pathnameChanges({ current: at("/a", { tab: 1 }), next: at("/a", { tab: 2 }) })).toBe(false);
    expect(pathnameChanges({ current: at("/a"), next: at("/b") })).toBe(true);
  });
});
