// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, retainSearchParams } from "@tanstack/react-router";
import { isValidElement, type ReactElement, type ReactNode } from "react";
import { AppRuntimeProvider, ToastProvider, aliasRouteHref, createRouteHref, pageChildren, pageElementProps, type ListProps } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  // The people workbench's scope counts; the list declarations do not read them.
  useAuthoredQuery: () => ({ data: undefined, isFetching: false, error: null }),
}));

import { enPartiesMessages } from "./i18n";
import parties from "./index";
import { MergeSelectionAction, mergeSelectionActions } from "./merge-selection";
import { OrganizationsPage } from "./OrganizationsPage";
import { PeoplePage } from "./PeoplePage";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties-merge" });
afterEach(() => {
  cleanup();
  clearClients();
});

const MERGE = enPartiesMessages["merge.selection.action"]!;
const MERGE_PATH = parties.routes!.find((route) => route.name === "parties.merge")!.path;
// Another app mounts the people list (`"desk.people": { parent: "desk", mount: "parties.people", path: "people" }`):
// composition adds its alias routes, and on that page `useRouteHref` builds the mounted
// routes' names as the alias's while every other name, the comparison's too, builds unchanged.
const MOUNTED_PEOPLE = "/desk/people";
const mountedRouteHref = aliasRouteHref(
  createRouteHref([
    ...(parties.routes ?? []),
    { name: "desk.people", path: MOUNTED_PEOPLE },
    { name: "desk.people.record", path: `${MOUNTED_PEOPLE}/$id` },
  ]),
  new Map([["parties.people", "desk.people"], ["parties.people.record", "desk.people.record"]]),
);

/** The selection bar's Merge for `ids` on the mounted people list, which a scope search narrows. */
function renderMountedSelection(ids: readonly string[], search = "peopleScope=UNASSIGNED", collection?: object) {
  // The app's root keeps `?app=` across links and redirects, as `createApp` declares it.
  const root = createRootRoute({ search: { middlewares: [retainSearchParams(["app"])] } });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: MOUNTED_PEOPLE,
        component: () => <MergeSelectionAction ids={new Set(ids)} collection={collection} />,
      }),
      createRoute({ getParentRoute: () => root, path: MERGE_PATH, component: () => <div>Comparison</div> }),
    ]),
    history: createMemoryHistory({ initialEntries: [`${MOUNTED_PEOPLE}?${search}`] }),
  });
  render(
    <Provider>
      <AppRuntimeProvider runtime={{ routeHref: mountedRouteHref }}>
        <ToastProvider><RouterProvider router={router} /></ToastProvider>
      </AppRuntimeProvider>
    </Provider>,
  );
  return router;
}

/** The first `List` a page declares, through its fragments and resource list. */
function listDeclaration(node: ReactNode): ListProps | undefined {
  for (const child of pageChildren(node)) {
    const list = pageElementProps<ListProps>(child, "list");
    if (list) return list;
    const nested = isValidElement<{ children?: ReactNode }>(child) ? listDeclaration(child.props.children) : undefined;
    if (nested) return nested;
  }
  return undefined;
}

describe("Merge in a parties list's selection bar", () => {
  test("two selected people open their comparison, and Back returns to the list in the app that mounts it", async () => {
    const router = renderMountedSelection(["ada", "grace"]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    await screen.findByText("Comparison");
    expect(router.state.location.pathname).toBe(createRouteHref(parties.routes ?? [])("parties.merge", { left: "ada", right: "grace" }));
    router.history.back();
    await waitFor(() => expect(router.state.location.pathname).toBe(MOUNTED_PEOPLE));
    expect(router.state.location.search).toEqual({ peopleScope: "UNASSIGNED" });
  });

  test("the comparison opens in the app the list was opened in", async () => {
    const router = renderMountedSelection(["ada", "grace"], "app=console");

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    await screen.findByText("Comparison");
    expect(router.state.location.search).toEqual({ app: "console" });
  });

  test("three selected open the batch dialog with the selection as one set", async () => {
    renderMountedSelection(["ada", "grace", "joan"]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText(enPartiesMessages["duplicates.title"]!)).toBeTruthy();
    expect(screen.queryByText("Comparison")).toBeNull();
  });

  test("one selected opens Merge into, where the person chooses the other record", async () => {
    const router = renderMountedSelection(["ada"]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText(enPartiesMessages["person.action.mergeOther"]!)).toBeTruthy();
    expect(router.state.location.pathname).toBe(MOUNTED_PEOPLE);
  });

  test.each([
    {
      state: "three a grouping cannot place",
      ids: ["ada", "grace", "joan"],
      collection: { selectedGroups: null },
      reason: enPartiesMessages["merge.selection.unplaceable"]!,
    },
    {
      state: "three in groups of one",
      ids: ["ada", "grace", "joan"],
      collection: {
        selectedGroups: ["ada", "grace", "joan"].map((id) => ({ field: "display_name", key: id, label: id, parents: [], ids: [id] })),
      },
      reason: enPartiesMessages["merge.selection.noGroups"]!,
    },
  ])("with $state, Merge stays in the bar, disabled, and says why", async ({ ids, collection, reason }) => {
    const router = renderMountedSelection(ids, "peopleScope=UNASSIGNED", collection);
    const merge = await screen.findByRole("button", { name: MERGE });

    expect(merge.getAttribute("aria-disabled")).toBe("true");
    fireEvent.mouseEnter(merge);
    expect(screen.getByText(reason)).toBeTruthy();
    fireEvent.click(merge);
    expect(router.state.location.pathname).toBe(MOUNTED_PEOPLE);
    expect(screen.queryByText("Comparison")).toBeNull();
  });

  test("the people and organizations lists add Merge to their selection and keep the resource's default selectability", async () => {
    let people: ReactElement | undefined;
    function PeopleDeclarations(): null {
      people = PeoplePage();
      return null;
    }
    const root = createRootRoute({ component: PeopleDeclarations });
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
    render(<Provider><AppRuntimeProvider runtime={{}}><ToastProvider><RouterProvider router={router} /></ToastProvider></AppRuntimeProvider></Provider>);
    await waitFor(() => expect(people).toBeDefined());

    for (const page of [people, OrganizationsPage()]) {
      const list = listDeclaration(page);
      expect(list?.bulkActions).toBe(mergeSelectionActions);
      expect(list?.selectable).toBeUndefined();
    }
  });
});
