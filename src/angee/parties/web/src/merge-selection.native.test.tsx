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
  type AllMatchingGroups,
  type ListProps,
  type SelectedGroup,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, describe, expect, test, vi } from "vitest";

const reads = vi.hoisted(() => ({ variables: [] as unknown[] }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  // The people workbench's scope counts and the batch dialog's preview; each read's variables are kept.
  useAuthoredQuery: (_document: unknown, variables: unknown, options?: { enabled?: boolean }) => {
    // Only the reads the hook sends: a disabled query sends none.
    if (options?.enabled !== false) reads.variables.push(variables);
    return { data: undefined, isFetching: false, error: null };
  },
}));

import { enPartiesMessages } from "./i18n";
import parties from "./index";
import { groupByVariables } from "@angee/refine";
import {
  MergeSelectionAction,
  ORGANIZATION_MERGE_GROUPS,
  PEOPLE_MERGE_GROUPS,
  organizationMergeSelectionActions,
  peopleMergeSelectionActions,
} from "./merge-selection";
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
  allMatching?: Parameters<typeof MergeSelectionAction>[0]["allMatching"],
  groups = PEOPLE_MERGE_GROUPS,
) {
  // The app's root keeps `?app=` across links and redirects, as `createApp` declares it.
  const root = createRootRoute({ search: { middlewares: [retainSearchParams(["app"])] } });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: MOUNTED_PEOPLE,
        component: () => (
          <MergeSelectionAction ids={new Set(ids)} selectedGroups={selectedGroups} allMatching={allMatching} groups={groups} />
        ),
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

    for (const [page, actions] of [
      [people, peopleMergeSelectionActions], [OrganizationsPage(), organizationMergeSelectionActions],
    ] as const) {
      const list = listDeclaration(page);
      expect(list?.bulkActions).toBe(actions);
      expect(list?.selectAllMatching).toBe(true);
      expect(list?.selectable).toBeUndefined();
    }
  });

  describe("under Select all", () => {
    // The People list grouped by name, repeated: its read as the list's own groups request sent it, order included.
    const byName: AllMatchingGroups = {
      unit: "groups",
      total: 120,
      read: {
        where: { display_name: { _is_null: false } },
        dimensions: [{ input: "DISPLAY_NAME", key: "display_name" }],
        having: { count_gt: 1 },
        orderBy: [{ field: "display_name", direction: "ASC", nulls: "LAST" }],
      },
    };

    test("a list grouped by a name opens the dialog on every group, its read forwarded unchanged", async () => {
      renderMountedSelection(["ada"], "peopleScope=UNASSIGNED", [], byName);

      fireEvent.click(await screen.findByRole("button", { name: MERGE }));

      expect(await screen.findByText(enPartiesMessages["duplicates.title"]!)).toBeTruthy();
      // The list's own request, its order included, with the first page and each group's rows: no order of the reader's.
      expect(reads.variables).toContainEqual({
        ...groupByVariables({ ...byName.read, pageSize: 50 }),
        rows_limit: 11,
      });
      expect(groupByVariables(byName.read)).toMatchObject({
        order_by: [{ field: "display_name", direction: "ASC", nulls: "LAST" }],
      });
      // The loaded ticked row is not a selection of its own: no "Merge into…" for it.
      expect(screen.queryByText("Comparison")).toBeNull();
    });

    test("a flat list says to group it first", async () => {
      renderMountedSelection(["ada", "grace", "joan"], "peopleScope=UNASSIGNED", undefined, {
        unit: "records", total: 300, read: { where: {} },
      });

      const verb = await screen.findByRole("button", { name: MERGE });
      expect(verb.getAttribute("aria-disabled")).toBe("true");
      expect(description(verb)).toBe(enPartiesMessages["merge.selection.allRecords"]);
    });

    test("a date bucket, or a field the dialog cannot show and mark, says to group by a name", async () => {
      renderMountedSelection(["ada"], "peopleScope=UNASSIGNED", [], {
        ...byName, read: {
          ...byName.read, dimensions: [{ input: "CREATED_AT", key: "created_at_month", granularity: "MONTH" }],
        },
      });
      const verb = await screen.findByRole("button", { name: MERGE });
      expect(verb.getAttribute("aria-disabled")).toBe("true");
      expect(description(verb)).toBe(enPartiesMessages["merge.selection.allGrouping"]);
      cleanup();

      renderMountedSelection(["acme"], "peopleScope=UNASSIGNED", [], {
        ...byName, read: { ...byName.read, dimensions: [{ input: "LEGAL_NAME", key: "legal_name" }] },
      }, ORGANIZATION_MERGE_GROUPS);
      const organizations = await screen.findByRole("button", { name: MERGE });
      expect(description(organizations)).toBe(enPartiesMessages["merge.selection.allGrouping"]);
    });

    test("a grouping without repeated values only says to turn it on", async () => {
      renderMountedSelection(["ada"], "peopleScope=UNASSIGNED", [], {
        ...byName, read: { where: {}, dimensions: byName.read.dimensions },
      });

      const verb = await screen.findByRole("button", { name: MERGE });
      expect(verb.getAttribute("aria-disabled")).toBe("true");
      expect(description(verb)).toBe(enPartiesMessages["merge.selection.allRepeated"]);
    });

    test("an order of more than the grouped field is refused: the dialog pages by one field", async () => {
      renderMountedSelection(["ada"], "peopleScope=UNASSIGNED", [], {
        ...byName,
        read: {
          ...byName.read,
          orderBy: [
            { field: "display_name", direction: "ASC", nulls: "LAST" },
            { field: "given_name", direction: "ASC", nulls: "LAST" },
          ],
        },
      });

      const verb = await screen.findByRole("button", { name: MERGE });
      expect(verb.getAttribute("aria-disabled")).toBe("true");
      expect(description(verb)).toBe(enPartiesMessages["merge.selection.allGrouping"]);
    });

    test("a nulls-first order is refused: its null key would come first and no cursor steps past it", async () => {
      renderMountedSelection(["ada"], "peopleScope=UNASSIGNED", [], {
        ...byName,
        read: { ...byName.read, orderBy: [{ field: "display_name", direction: "ASC", nulls: "FIRST" }] },
      });

      const verb = await screen.findByRole("button", { name: MERGE });
      expect(verb.getAttribute("aria-disabled")).toBe("true");
      expect(description(verb)).toBe(enPartiesMessages["merge.selection.allGrouping"]);
    });
  });
});
