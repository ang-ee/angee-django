// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, retainSearchParams } from "@tanstack/react-router";
import { isValidElement, type ReactElement, type ReactNode } from "react";
import {
  AppRuntimeProvider,
  ToastProvider,
  aliasRouteHref,
  createRouteHref,
  pageChildren,
  pageElementProps,
  type ListProps,
  type SelectedGroup,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, describe, expect, test, vi } from "vitest";

const reads = vi.hoisted(() => ({ variables: [] as unknown[] }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  // The people workbench's scope counts and the batch dialog's preview; each read's variables are kept.
  useAuthoredQuery: (_document: unknown, variables: unknown) => {
    reads.variables.push(variables);
    return { data: undefined, isFetching: false, error: null };
  },
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
  reads.variables = [];
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
function renderMountedSelection(
  ids: readonly string[],
  search = "peopleScope=UNASSIGNED",
  selectedGroups?: readonly SelectedGroup[] | null,
) {
  // The app's root keeps `?app=` across links and redirects, as `createApp` declares it.
  const root = createRootRoute({ search: { middlewares: [retainSearchParams(["app"])] } });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: MOUNTED_PEOPLE,
        component: () => <MergeSelectionAction ids={new Set(ids)} selectedGroups={selectedGroups} />,
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

/** One group of a list grouped by name, holding the ticked `ids`. */
function nameGroup(key: string | null, ids: readonly string[]): SelectedGroup {
  return { field: "display_name", key, label: key ?? "No value", parents: [], ids };
}

/** The verb's accessible description: the text of the nodes its aria-describedby names. */
function description(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
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

  test("three selected on an ungrouped list ask the server for the duplicate sets among them", async () => {
    renderMountedSelection(["ada", "grace", "joan"]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText(enPartiesMessages["duplicates.title"]!)).toBeTruthy();
    expect(reads.variables).toContainEqual({ ids: ["ada", "grace", "joan"] });
    expect(reads.variables).not.toContainEqual(expect.objectContaining({ sets: [["ada", "grace", "joan"]] }));
    expect(screen.queryByText("Comparison")).toBeNull();
  });

  test("a grouped list's selection is one set per group: two groups of two are two sets, never one of four", async () => {
    renderMountedSelection(["ada", "grace", "joan", "mary"], undefined, [
      nameGroup("Ada Example", ["ada", "grace"]),
      nameGroup("Joan Example", ["joan", "mary"]),
    ]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText(enPartiesMessages["duplicates.title"]!)).toBeTruthy();
    expect(reads.variables).toContainEqual({
      sets: [["ada", "grace"], ["joan", "mary"]], certain: false, groupings: [["display_name"], ["display_name"]],
    });
    expect(reads.variables).not.toContainEqual(expect.objectContaining({ sets: [["ada", "grace", "joan", "mary"]] }));
  });

  test("a group of one and a group without a value are left out, and the dialog says so", async () => {
    renderMountedSelection(["ada", "grace", "joan", "mary", "nell"], undefined, [
      nameGroup("Ada Example", ["ada", "grace"]),
      nameGroup("Joan Example", ["joan"]),
      nameGroup(null, ["mary"]),
      nameGroup("", ["nell"]),
    ]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText("Left out, with no one else selected in their group: 1.")).toBeTruthy();
    expect(screen.getByText("Left out, in a group without a value: 2.")).toBeTruthy();
    expect(reads.variables).toContainEqual({ sets: [["ada", "grace"]], certain: false, groupings: [["display_name"]] });
  });

  test("more groups than one batch takes merge the first fifty, and the dialog says how to reach the rest", async () => {
    const groups = Array.from({ length: 60 }, (_, index) => nameGroup(`Person ${index}`, [`a${index}`, `b${index}`]));
    renderMountedSelection(groups.flatMap((group) => group.ids), undefined, groups);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText(
      "This merges the first 50 of 60 groups. The selection clears after the merge: select the rest and choose Merge… again.",
    )).toBeTruthy();
    expect(reads.variables).toContainEqual({
      sets: groups.slice(0, 50).map((group) => [...group.ids]), certain: false, groupings: groups.slice(0, 50).map(() => ["display_name"]),
    });
  });

  test("three ticked on a grouped list that cannot place them are not offered as a batch", async () => {
    renderMountedSelection(["ada", "grace", "joan"], undefined, null);

    const verb = await screen.findByRole("button", { name: MERGE });
    expect(verb.getAttribute("aria-disabled")).toBe("true");
    expect(description(verb)).toBe(enPartiesMessages["merge.selection.unplaceable"]);
    fireEvent.click(verb);
    expect(screen.queryByText(enPartiesMessages["duplicates.title"]!)).toBeNull();
  });

  test("a grouping that holds no group of two or more with a value offers no batch", async () => {
    renderMountedSelection(["ada", "grace", "joan"], undefined, [
      nameGroup("Ada Example", ["ada"]),
      nameGroup(null, ["grace", "joan"]),
    ]);

    const verb = await screen.findByRole("button", { name: MERGE });
    expect(verb.getAttribute("aria-disabled")).toBe("true");
    expect(description(verb)).toBe(enPartiesMessages["merge.selection.noGroups"]);
  });

  test("two ticked open their comparison whatever the grouping says", async () => {
    renderMountedSelection(["ada", "grace"], undefined, null);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText("Comparison")).toBeTruthy();
  });

  test("one selected opens Merge into, where the person chooses the other record", async () => {
    const router = renderMountedSelection(["ada"]);

    fireEvent.click(await screen.findByRole("button", { name: MERGE }));

    expect(await screen.findByText(enPartiesMessages["person.action.mergeOther"]!)).toBeTruthy();
    expect(router.state.location.pathname).toBe(MOUNTED_PEOPLE);
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
