// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, type RouteComponent } from "@tanstack/react-router";
import { AppRuntimeProvider, ModalsHost, ToastProvider, createRouteHref } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

const doubles = vi.hoisted(() => {
  const party = (id: string, display_name: string) => ({
    id, display_name, display_avatar: null, notes: null, first_met_note: null, merge_scalar_fields: ["display_name"],
    handles: [], circle_members: [], relationships: [], inbound_relationships: [],
  });
  return {
    comparison: { left: party("ada", "Ada Example"), right: party("grace", "A. Example"), left_person: null, right_person: null },
    mutate: vi.fn(async (_variables: Readonly<Record<string, unknown>>) => ({ dismiss_same_as: { id: "pair-1" } })),
  };
});
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: doubles.comparison, isFetching: false, error: null }),
  useAuthoredMutation: () => [doubles.mutate, { fetching: false, error: null, reset: () => undefined }],
}));

import { enPartiesMessages } from "./i18n";
import parties from "./index";
import { MergePage } from "./MergePage";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties-merge-page" });
afterEach(() => {
  cleanup();
  clearClients();
  doubles.mutate.mockClear();
});

const routeHref = createRouteHref(parties.routes ?? []);
const COMPARISON = routeHref("parties.merge", { left: "ada", right: "grace" });
// The people list as another app mounts it, narrowed by its scope search.
const MOUNTED_PEOPLE = "/desk/people?peopleScope=UNASSIGNED";
const CANCEL = enPartiesMessages["merge.cancel"]!;

function renderComparison(entries: readonly string[]) {
  const root = createRootRoute();
  const page = (path: string, component: RouteComponent) => createRoute({ getParentRoute: () => root, path, component });
  const router = createRouter({
    routeTree: root.addChildren([
      page("/desk/people", () => <div>Mounted people</div>),
      page(routeHref("parties.review"), () => <div>Review queue</div>),
      page(parties.routes!.find((route) => route.name === "parties.merge")!.path, MergePage),
    ]),
    history: createMemoryHistory({ initialEntries: [...entries] }),
  });
  render(
    <Provider><AppRuntimeProvider runtime={{ routeHref }}><ModalsHost><ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider></ModalsHost></AppRuntimeProvider></Provider>,
  );
  return router;
}

test("Cancel returns to the list the comparison was opened from, in the app that mounts it", async () => {
  const router = renderComparison([MOUNTED_PEOPLE, COMPARISON]);

  fireEvent.click(await screen.findByRole("button", { name: CANCEL }));

  await screen.findByText("Mounted people");
  expect(router.state.location.search).toEqual({ peopleScope: "UNASSIGNED" });
});

test("Keep separate saves the decision, then returns to the list it was opened from", async () => {
  const router = renderComparison([MOUNTED_PEOPLE, COMPARISON]);

  fireEvent.click(await screen.findByRole("button", { name: enPartiesMessages["merge.dismiss"]! }));

  await screen.findByText("Mounted people");
  expect(doubles.mutate).toHaveBeenCalledWith({ aId: "ada", bId: "grace" });
  expect(router.state.location.search).toEqual({ peopleScope: "UNASSIGNED" });
});

test("a comparison opened directly goes back to the review queue, its route parent", async () => {
  const router = renderComparison([COMPARISON]);

  fireEvent.click(await screen.findByRole("button", { name: CANCEL }));

  await screen.findByText("Review queue");
  expect(router.state.location.pathname).toBe(routeHref("parties.review"));
});
