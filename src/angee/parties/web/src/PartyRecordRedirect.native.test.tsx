// @vitest-environment happy-dom
import { render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, type RouteComponent } from "@tanstack/react-router";
import { AppRuntimeProvider, createRouteHref, useRouteParam } from "@angee/ui";
import { expect, test, vi } from "vitest";

const query = vi.hoisted(() => ({ kind: "PERSON", survivor: "party-1", none: false }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: {
    party_record: query.none ? null : { id: query.survivor, concrete_kind: query.kind },
    party_statements_by_pk: { id: "handle-1", subject: { id: "party-1" } },
  }, isFetching: false, error: null }),
}));

import parties from "./index";
import { PartyRecordRedirect, renderPartyUnavailable } from "./PartyRecordRedirect";
import { PartyStatementRedirect } from "./PartyStatementRedirect";

const routeHref = createRouteHref(parties.routes ?? []);
// The id a concrete page reads no record for: its form's miss renders the parties fallback.
const MISSED = "party-gone";

function concretePage(label: string): RouteComponent {
  return function ConcretePage() {
    const id = useRouteParam("id") ?? "";
    return id === MISSED ? <>{renderPartyUnavailable(id, <p>Record unavailable</p>)}</> : <div>{`${label} ${id}`}</div>;
  };
}

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
      route("parties.statements.record", PartyStatementRedirect),
      route("parties.people.record", concretePage("Person")),
      route("parties.organizations.record", concretePage("Organization")),
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

test("a merged-away party's record redirects to its survivor", async () => {
  query.kind = "PERSON";
  query.survivor = "party-2";
  try {
    const router = renderRedirects();
    await router.navigate({ href: routeHref("parties.records.record", { id: "party-1" }) });
    await waitFor(() => expect(router.state.location.pathname).toBe(routeHref("parties.people.record", { id: "party-2" })));
  } finally {
    query.survivor = "party-1";
  }
});

test("the handle-to-party redirect chain keeps the trail and identity selection", async () => {
  query.kind = "PERSON";
  const router = renderRedirects();
  await router.navigate({
    href: routeHref("parties.statements.record", { id: "handle-1" }, { view: "all" }),
    state: { breadcrumbTrail: trail },
  });
  await waitFor(() => expect(router.state.location.pathname).toBe(routeHref("parties.people.record", { id: "party-1" })));
  expect(router.state.location.state.breadcrumbTrail).toEqual(trail);
  expect(router.state.location.search).toEqual({ view: "all", recordTab: "identity", partyStatement: "handle-1" });
  router.history.back();
  await waitFor(() => expect(router.state.location.pathname).toBe("/start"));
});

test.each([
  ["PERSON", "parties.people.record", "Person"],
  ["ORGANIZATION", "parties.organizations.record", "Organization"],
])("a %s page that reads no record opens its survivor's page, keeping the app", async (kind, destination, label) => {
  // A reader of both records: party_record answers the merged-away id with its survivor.
  Object.assign(query, { kind, survivor: "party-2", none: false });
  try {
    const router = renderRedirects();
    await router.navigate({ href: routeHref(destination, { id: MISSED }, { app: "contacts" }) });
    await waitFor(() => expect(router.state.location.pathname).toBe(routeHref(destination, { id: "party-2" })));
    expect(router.state.location.search).toEqual({ app: "contacts" });
    expect(await screen.findByText(`${label} party-2`)).toBeTruthy();
  } finally {
    Object.assign(query, { kind: "PERSON", survivor: "party-1", none: false });
  }
});

test.each([
  "a reader of the merged record only",
  "a reader of the survivor only",
  "a stranger",
  "a trashed person who was never merged",
])("%s stays on the page's unavailable state", async () => {
  // party_record answers null to each: no survivor is revealed and nothing is followed.
  query.none = true;
  try {
    const router = renderRedirects();
    await router.navigate({ href: routeHref("parties.people.record", { id: MISSED }) });
    expect(await screen.findByText("Record unavailable")).toBeTruthy();
    expect(router.state.location.pathname).toBe(routeHref("parties.people.record", { id: MISSED }));
  } finally {
    query.none = false;
  }
});

test("a missed id that resolves to itself is not followed back to the page that missed", async () => {
  query.survivor = MISSED;
  try {
    const router = renderRedirects();
    await router.navigate({ href: routeHref("parties.people.record", { id: MISSED }) });
    expect(await screen.findByText("Record unavailable")).toBeTruthy();
    expect(router.state.location.pathname).toBe(routeHref("parties.people.record", { id: MISSED }));
  } finally {
    query.survivor = "party-1";
  }
});
