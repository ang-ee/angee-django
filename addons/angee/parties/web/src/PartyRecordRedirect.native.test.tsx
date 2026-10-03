// @vitest-environment happy-dom
import { cleanup, render, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, type RouteComponent } from "@tanstack/react-router";
import { AppRuntimeProvider, createRouteHref } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";

const query = vi.hoisted(() => ({ kind: "PERSON" }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: {
    parties_by_pk: { id: "party-1", concrete_kind: query.kind },
    party_handles_by_pk: { id: "handle-1", party: { id: "party-1" } },
  }, isFetching: false, error: null }),
}));

import parties from "./index";
import { PartyRecordRedirect } from "./PartyRecordRedirect";
import { PartyHandleRedirect } from "./PartyHandleRedirect";

afterEach(cleanup);

const routeHref = createRouteHref(parties.routes ?? []);
const trail = [{ label: "Messages", href: "/messages" }, { label: "Retained message", href: "/messages/msg-1" }];

function renderRedirects() {
  const root = createRootRoute();
  const route = (name: string, component: RouteComponent) => createRoute({
    getParentRoute: () => root,
    path: parties.routes!.find((declaration) => declaration.name === name)!.path,
    component,
    validateSearch: (search: Record<string, unknown>) => search,
  });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/start", component: () => <div>Start</div> }),
      route("parties.records.record", PartyRecordRedirect),
      route("parties.handle-links.record", PartyHandleRedirect),
      route("parties.people.record", () => <div>Person</div>),
      route("parties.organizations.record", () => <div>Organization</div>),
    ]),
    history: createMemoryHistory({ initialEntries: ["/start"] }),
  });
  render(<AppRuntimeProvider runtime={{ routeHref }}><RouterProvider router={router} /></AppRuntimeProvider>);
  return router;
}

test.each([
  ["PERSON", "parties.people.record"],
  ["ORGANIZATION", "parties.organizations.record"],
])("a %s record redirect preserves breadcrumb history and search", async (kind, destination) => {
  query.kind = kind;
  const router = renderRedirects();
  await router.navigate({
    href: routeHref("parties.records.record", { id: "party-1" }, { recordTab: "identity" }),
    state: { breadcrumbTrail: trail },
  });
  await waitFor(() => expect(router.state.location.pathname).toBe(routeHref(destination, { id: "party-1" })));
  expect(router.state.location.state.breadcrumbTrail).toEqual(trail);
  expect(router.state.location.search).toEqual({ recordTab: "identity" });
  router.history.back();
  await waitFor(() => expect(router.state.location.pathname).toBe("/start"));
});

test("the handle-to-party redirect chain keeps the trail and identity selection", async () => {
  query.kind = "PERSON";
  const router = renderRedirects();
  await router.navigate({
    href: routeHref("parties.handle-links.record", { id: "handle-1" }, { view: "all" }),
    state: { breadcrumbTrail: trail },
  });
  await waitFor(() => expect(router.state.location.pathname).toBe(routeHref("parties.people.record", { id: "party-1" })));
  expect(router.state.location.state.breadcrumbTrail).toEqual(trail);
  expect(router.state.location.search).toEqual({ view: "all", recordTab: "identity", partyHandle: "handle-1" });
  router.history.back();
  await waitFor(() => expect(router.state.location.pathname).toBe("/start"));
});
