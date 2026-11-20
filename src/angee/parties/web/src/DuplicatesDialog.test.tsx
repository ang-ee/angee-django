// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, retainSearchParams, useRouter,
} from "@tanstack/react-router";
import * as React from "react";
import {
  AppRuntimeProvider, InAppLinkProvider, createRouteHref, routerNavigator, type AllMatchingGroups,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

type Read = {
  data: unknown; isFetching: boolean; error: Error | null; isFetchedAfterMount?: boolean; refetch?: () => unknown;
};
const mocks = vi.hoisted(() => ({
  merge: vi.fn(), invalidate: vi.fn(), invalidateResource: vi.fn(), previews: [] as readonly unknown[],
  variables: [] as unknown[], selection: undefined as unknown,
  /** Re-renders the dialog's host, as a query's settled read would. */
  rerender: undefined as (() => void) | undefined,
  // Reads answered per document when a test sets them: the certain pairs' pages, and each page of previews.
  documents: { certain: undefined as unknown, previews: undefined as unknown, groups: undefined as unknown },
  certainRead: undefined as ((variables: { after: string | null }) => Read) | undefined,
  groupsRead: undefined as ((variables: { where?: unknown; limit: number; offset?: number }) => Read) | undefined,
  previewRead: undefined as ((variables: { sets: string[][] }) => Read) | undefined,
}));
vi.mock("@angee/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/metadata")>()),
  useResourceInvalidates: (models: readonly string[]) => models.map((resource) => ({ resource, invalidates: ["resourceAll"] })),
}));
vi.mock("@refinedev/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@refinedev/core")>()),
  useInvalidate: () => mocks.invalidateResource,
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (document: unknown, variables: never, options?: { enabled?: boolean }) => {
    // Only the reads the hook sends: a disabled query sends none.
    if (options?.enabled !== false) mocks.variables.push(variables);
    if (document === mocks.documents.certain && mocks.certainRead) return mocks.certainRead(variables);
    if (document === mocks.documents.groups && mocks.groupsRead) return mocks.groupsRead(variables);
    if (document === mocks.documents.previews && mocks.previewRead) return mocks.previewRead(variables);
    return {
      data: { duplicate_set_previews: mocks.previews, selection_duplicate_sets: mocks.selection },
      isFetching: false, isFetchedAfterMount: true, error: null,
    };
  },
  useAuthoredMutation: () => [mocks.merge, { fetching: false, error: null, reset: () => undefined }],
  useInvalidateAuthoredModels: () => mocks.invalidate,
}));

import { DuplicatesDialog, type DuplicateSetInput, type DuplicatesDialogProps } from "./DuplicatesDialog";
import { groupByVariables } from "@angee/refine";
import { CertainSameAsPairs, DuplicateSetPreviews, PARTY_MERGE_RESOURCES, PeopleGroupSets } from "./documents";
import { enPartiesMessages } from "./i18n";
import parties from "./index";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties-duplicates" });
beforeEach(() => {
  mocks.merge.mockReset();
  mocks.invalidate.mockReset();
  mocks.invalidateResource.mockReset();
  mocks.previews = PREVIEWS;
  mocks.variables = [];
  mocks.selection = undefined;
  mocks.documents = { certain: CertainSameAsPairs, previews: DuplicateSetPreviews, groups: PeopleGroupSets };
  mocks.certainRead = undefined;
  mocks.groupsRead = undefined;
  mocks.previewRead = undefined;
});
afterEach(() => {
  cleanup();
  clearClients();
});

const routeHref = createRouteHref(parties.routes ?? []);
const MERGE_PATH = parties.routes!.find((route) => route.name === "parties.merge")!.path;
const message = (key: string, vars: Record<string, string> = {}) =>
  Object.entries(vars).reduce((text, [name, value]) => text.replace(`{${name}}`, value), enPartiesMessages[key]!);

function member(id: string, display_name: string, address: string, created_at: string) {
  const handle = { id: `${id}-handle`, platform: "EMAIL", value: address };
  return {
    id, display_name, concrete_kind: "PERSON", created_at, first_met_note: "",
    statements: [{ id: `${id}-statement`, source_kind: "MAILBOX", handle }],
  };
}

const PREVIEWS = [
  {
    outcome: null,
    reason: "",
    digest: "digest-ada",
    survivor: { id: "ada" },
    members: [
      member("ada", "Ada Lovelace", "ada@example.test", "2020-01-01T00:00:00Z"),
      member("augusta", "Augusta King", "augusta@example.test", "2021-01-01T00:00:00Z"),
    ],
    disagreements: [{ field: "given_name", values: ["Ada", "Augusta"], shown: "Ada" }],
    fills: [],
    grouping: [],
  },
  {
    outcome: "REFUSED",
    reason: "A person kept two of these records apart.",
    digest: "",
    survivor: null,
    members: [
      member("grace", "Grace Hopper", "grace@example.test", "2020-02-01T00:00:00Z"),
      member("amazing-grace", "Amazing Grace", "amazing@example.test", "2020-03-01T00:00:00Z"),
    ],
    disagreements: [],
    fills: [],
    grouping: [],
  },
  {
    outcome: null,
    reason: "",
    digest: "digest-joan",
    survivor: { id: "joan" },
    members: [
      member("joan", "Joan Clarke", "joan@example.test", "2020-04-01T00:00:00Z"),
      member("joan-2", "Joan Clarke", "joan@example.test", "2020-05-01T00:00:00Z"),
    ],
    disagreements: [],
    fills: [],
    grouping: [],
  },
];

const SETS: readonly DuplicateSetInput[] = [
  { members: ["augusta", "ada"], heading: "Lovelace" },
  { members: ["grace", "amazing-grace"] },
  { members: ["joan", "joan-2"] },
];
const merged = (id: string, name: string) => ({
  members: [id], outcome: "MERGED", reason: "", survivor: { id, display_name: name, concrete_kind: "PERSON" },
});

/** The dialog over the review queue of the app opened with `?app=console`, links routed as the host routes them. */
function renderDialog(
  onClose = vi.fn(), sets = SETS, certain = false, selection?: readonly string[], allCertain?: { total: string },
  allGroups?: DuplicatesDialogProps["allGroups"],
) {
  function Host(): React.ReactElement {
    const [, rerender] = React.useReducer((count: number) => count + 1, 0);
    mocks.rerender = rerender;
    const router = useRouter();
    const navigate = React.useMemo(() => routerNavigator(router), [router]);
    return (
      <InAppLinkProvider navigate={navigate}>
        <DuplicatesDialog
          sets={sets} selection={selection} allCertain={allCertain} allGroups={allGroups} certain={certain} onClose={onClose}
        />
        <Outlet />
      </InAppLinkProvider>
    );
  }
  // The app's root keeps `?app=` across links and redirects, as `createApp` declares it.
  const root = createRootRoute({ component: Host, search: { middlewares: [retainSearchParams(["app"])] } });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/parties/review", component: () => null }),
      createRoute({ getParentRoute: () => root, path: MERGE_PATH, component: () => <div>Comparison</div> }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/parties/review?app=console"] }),
  });
  render(<Provider><AppRuntimeProvider runtime={{ routeHref }}><RouterProvider router={router} /></AppRuntimeProvider></Provider>);
  return { router, onClose };
}

/** Set n is p<n> and q<n>, one address; every tenth one's records disagree on a name, so it is warned. */
const preview = (number: number) => ({
  outcome: null, reason: "", digest: `digest-${number}`, survivor: { id: `p${number}` },
  members: [
    member(`p${number}`, `Person ${number}`, `p${number}@example.test`, "2020-01-01T00:00:00Z"),
    member(`q${number}`, `Person ${number}`, `p${number}@example.test`, "2021-01-01T00:00:00Z"),
  ],
  disagreements: number % 10 === 0 ? [{ field: "given_name", values: ["P", "Q"], shown: "P" }] : [],
  fills: [], grouping: [],
});
const previewsOf = (variables: { sets: string[][] }) => variables.sets.map(([left]) => preview(Number(left!.slice(1))));
/** Each page's previews, one stable result per page as the query cache keeps it. */
const answering = () => {
  const cache = new Map<string, Read>();
  return (variables: { sets: string[][] }): Read => {
    const key = JSON.stringify(variables.sets);
    if (!cache.has(key)) {
      cache.set(key, {
        data: { duplicate_set_previews: previewsOf(variables) }, isFetching: false, isFetchedAfterMount: true, error: null,
      });
    }
    return cache.get(key)!;
  };
};

/** A paged run lists and previews page after page (forty pages at the thousand-set bound) and may send its chunks:
 * about 2 s alone, up to 15.2 s measured on a runner at a load average of 60, over Vitest's 5 s default. */
const PAGED_RUN_TIMEOUT_MS = 30_000;

describe("the batch merge dialog", () => {
  test("lists each set's records with their addresses, which survives, the disagreements and the refusals", async () => {
    renderDialog();

    expect(await screen.findByText("Lovelace")).toBeTruthy();
    // Ada's set and Joan's each name their survivor; the refused set names none.
    expect(screen.getAllByText(message("duplicates.survives"))).toHaveLength(2);
    expect(screen.getByText("augusta@example.test")).toBeTruthy();
    expect(screen.getByText("Augusta")).toBeTruthy();
    expect(screen.getByText(message("duplicates.refused", { reason: "A person kept two of these records apart." }))).toBeTruthy();
    expect(screen.getByText(new RegExp(message("duplicates.undo")))).toBeTruthy();
    // Only the set without a warning is included at first; the refused set has no toggle.
    expect(screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-checked"))).toEqual(["false", "true"]);
    expect(screen.getAllByText(message("duplicates.includeWarned"))).toHaveLength(1);
    expect(screen.getByRole("button", { name: message("duplicates.confirm", { count: "1" }) })).toBeTruthy();
  });

  test("a dialog opened with its sets never reads what its disabled listing read answers from the cache", async () => {
    // The listing read is disabled unless the dialog pages, but a query cached under its key still answers; here the
    // answer is no listing page at all (no certain pairs, no groups), so reading it would throw before any set shows.
    mocks.certainRead = () => ({ data: { cached: true }, isFetching: false, error: null });
    renderDialog();

    expect(await screen.findByText("Lovelace")).toBeTruthy();
    expect(screen.getByRole("button", { name: message("duplicates.confirm", { count: "1" }) })).toBeTruthy();
  });

  test("an error cached on the disabled listing read does not show in a dialog opened with its sets", async () => {
    mocks.certainRead = () => ({ data: undefined, isFetching: false, isFetchedAfterMount: false, error: new Error("old") });
    renderDialog();

    expect(await screen.findByText("Lovelace")).toBeTruthy();
    expect(screen.queryByRole("button", { name: message("duplicates.retry") })).toBeNull();
    expect(screen.queryByText(message("duplicates.error"))).toBeNull();
  });

  test("a dialog reopened within the cache window reads its previews again and ticks from that answer", async () => {
    // An earlier dialog left these sets' previews in the cache: then Ada's records agreed; read now, they disagree.
    let reread = false;
    const cached: Read = {
      data: { duplicate_set_previews: [{ ...PREVIEWS[0]!, disagreements: [] }, PREVIEWS[1]!, PREVIEWS[2]!] },
      isFetching: false, isFetchedAfterMount: false, error: null,
      refetch: () => { reread = true; mocks.rerender?.(); },
    };
    const fresh: Read = {
      data: { duplicate_set_previews: PREVIEWS }, isFetching: false, isFetchedAfterMount: true, error: null,
    };
    mocks.previewRead = () => (reread ? fresh : cached);
    renderDialog();

    // Built from the fresh answer: Ada's set is warned and waits for a tick, so only Joan's is included.
    expect(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "1" }) })).toBeTruthy();
    expect(reread).toBe(true);
  });

  test("one confirm merges only the included sets, then shows each outcome and the sets left out", async () => {
    mocks.merge.mockResolvedValue({ merge_duplicates: [merged("joan", "Joan Clarke")] });
    const { onClose } = renderDialog();

    fireEvent.click(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "1" }) }));

    expect(await screen.findByText(message("duplicates.outcome.merged", { name: "Joan Clarke" }))).toBeTruthy();
    expect(mocks.merge).toHaveBeenCalledWith({
      sets: [["joan", "joan-2"]], digests: ["digest-joan"], certain: false, groupings: null,
    }, { errorNotification: false });
    expect(screen.getByText(message("duplicates.outcome.left"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: message("duplicates.done") }));
    expect(onClose).toHaveBeenCalledWith(true);
  });

  test("a set with a warning merges once the person ticks it, and an unticked set is not sent", async () => {
    mocks.merge.mockResolvedValue({ merge_duplicates: [merged("ada", "Ada Lovelace")] });
    renderDialog();
    const [warnedSet, cleanSet] = await screen.findAllByRole("checkbox");

    fireEvent.click(warnedSet!);
    fireEvent.click(cleanSet!);
    fireEvent.click(screen.getByRole("button", { name: message("duplicates.confirm", { count: "1" }) }));

    await waitFor(() => expect(mocks.merge).toHaveBeenCalledWith({
      sets: [["ada", "augusta"]], digests: ["digest-ada"], certain: false, groupings: null,
    }, { errorNotification: false }));
  });

  test("a disagreement's compare link opens the pair's comparison in the same app", async () => {
    const { router } = renderDialog();

    fireEvent.click(await screen.findByText(message("duplicates.compare", { name: "Augusta King" })));

    await waitFor(() => expect(router.state.location.pathname).toBe(routeHref("parties.merge", { left: "ada", right: "augusta" })));
    expect(router.state.location.search).toEqual({ app: "console" });
  });

  test("a member renamed since the list grouped it shows its current name and that it no longer matches", async () => {
    mocks.previews = [{
      outcome: null,
      reason: "",
      survivor: { id: "ada" },
      members: [
        member("ada", "Ada Lovelace", "ada@example.test", "2020-01-01T00:00:00Z"),
        member("ada-2", "Augusta King", "ada@example.test", "2021-01-01T00:00:00Z"),
      ],
      disagreements: [{ field: "display_name", values: ["Ada Lovelace", "Augusta King"], shown: "Ada Lovelace" }],
      fills: [],
      grouping: [],
    }];

    renderDialog(vi.fn(), [{
      members: ["ada", "ada-2"],
      heading: "Ada Lovelace",
      grouping: [{ field: "display_name", key: "Ada Lovelace" }],
    }]);

    expect((await screen.findAllByText("Augusta King")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(message("duplicates.moved"))).toHaveLength(1);
  });

  test("a warning that clears after the set was first shown leaves it unticked until the person ticks it", async () => {
    renderDialog();
    const [warnedSet, cleanSet] = await screen.findAllByRole("checkbox");
    expect(warnedSet!.getAttribute("aria-checked")).toBe("false");

    // A refresh clears the disagreement: the set is no longer as the person first saw it.
    mocks.previews = [{ ...PREVIEWS[0]!, disagreements: [], digest: "digest-ada-agreed" }, PREVIEWS[1]!, PREVIEWS[2]!];
    fireEvent.click(cleanSet!);

    await waitFor(() => expect(screen.getByText(message("duplicates.includeChanged"))).toBeTruthy());
    expect(screen.getAllByRole("checkbox")[0]!.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByRole("button", { name: message("duplicates.confirm", { count: "0" }) })).toBeTruthy();
  });

  test("a refused set that becomes mergeable is not included until the person ticks it", async () => {
    mocks.merge.mockResolvedValue({ merge_duplicates: [merged("grace", "Grace Hopper")] });
    renderDialog();
    const [, cleanSet] = await screen.findAllByRole("checkbox");

    mocks.previews = [
      PREVIEWS[0]!,
      { ...PREVIEWS[1]!, outcome: null, reason: "", survivor: { id: "grace" }, digest: "digest-grace" },
      PREVIEWS[2]!,
    ];
    fireEvent.click(cleanSet!);

    await waitFor(() => expect(screen.getAllByRole("checkbox")).toHaveLength(3));
    const grace = screen.getAllByRole("checkbox")[1]!;
    expect(grace.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(grace);
    fireEvent.click(screen.getByRole("button", { name: message("duplicates.confirm", { count: "1" }) }));

    await waitFor(() => expect(mocks.merge).toHaveBeenCalledWith({
      sets: [["grace", "amazing-grace"]], digests: ["digest-grace"], certain: false, groupings: null,
    }, { errorNotification: false }));
  });

  test("a certain pair from Review whose only difference is a fill carries no warning, starts ticked and is counted", async () => {
    mocks.previews = [{
      ...PREVIEWS[2]!,
      fills: [{ field: "nickname", values: [null, "Jo"], shown: "Jo" }],
    }];
    renderDialog(vi.fn(), [{ members: ["joan", "joan-2"], heading: "Joan Clarke and Joan Clarke, both at joan@example.test" }], true);

    const [set] = await screen.findAllByRole("checkbox");
    expect(set!.getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText(message("duplicates.includeWarned"))).toBeNull();
    expect(screen.getByRole("button", { name: message("duplicates.confirm", { count: "1" }) })).toBeTruthy();
  });

  test("a set whose records state two different values starts unticked", async () => {
    mocks.previews = [PREVIEWS[0]!];
    renderDialog(vi.fn(), [SETS[0]!]);

    const [set] = await screen.findAllByRole("checkbox");
    expect(set!.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(message("duplicates.includeWarned"))).toBeTruthy();
  });

  test("in a grouped selection, a member whose grouping value was cleared is marked and its set starts unticked, its other differences fills only", async () => {
    mocks.previews = [{
      ...PREVIEWS[2]!,
      disagreements: [],
      fills: [
        { field: "family_name", values: ["Clarke", null], shown: "Clarke" },
        { field: "nickname", values: [null, "Jo"], shown: "Jo" },
      ],
    }];
    renderDialog(vi.fn(), [{
      members: ["joan", "joan-2"], heading: "Clarke", grouping: [{ field: "family_name", key: "Clarke" }],
    }]);

    const [set] = await screen.findAllByRole("checkbox");
    expect(set!.getAttribute("aria-checked")).toBe("false");
    expect(screen.getAllByText(message("duplicates.moved"))).toHaveLength(1);
  });

  test("a grouped set whose members all still match and differ only by fills starts ticked", async () => {
    mocks.previews = [{ ...PREVIEWS[2]!, disagreements: [], fills: [{ field: "nickname", values: [null, "Jo"], shown: "Jo" }] }];
    renderDialog(vi.fn(), [{
      members: ["joan", "joan-2"], heading: "Clarke", grouping: [{ field: "family_name", key: "Clarke" }],
    }]);

    const [set] = await screen.findAllByRole("checkbox");
    expect(set!.getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText(message("duplicates.moved"))).toBeNull();
  });

  test("a set with fills says what the survivor also gains and starts ticked; a set without says nothing", async () => {
    mocks.previews = [
      {
        ...PREVIEWS[2]!,
        fills: [
          { field: "nickname", values: [null, "Jo"], shown: "Jo" },
          { field: "birthday", values: ["1990-01-02", null], shown: "1990-01-02" },
        ],
      },
      {
        outcome: null, reason: "", digest: "digest-mary", survivor: { id: "mary" }, disagreements: [], fills: [], grouping: [],
        members: [
          member("mary", "Mary Example", "mary@example.test", "2020-06-01T00:00:00Z"),
          member("mary-2", "Mary Example", "mary@example.test", "2020-07-01T00:00:00Z"),
        ],
      },
    ];
    renderDialog(vi.fn(), [{ members: ["joan", "joan-2"] }, { members: ["mary", "mary-2"] }]);

    const fields = [enPartiesMessages["merge.field.nickname"]!, enPartiesMessages["merge.field.birthday"]!].join(", ");
    expect(await screen.findByText(message("duplicates.gains", { fields }))).toBeTruthy();
    expect(screen.getAllByText(/^The survivor also gains/)).toHaveLength(1);
    expect(screen.getAllByRole("checkbox").map((box) => box.getAttribute("aria-checked"))).toEqual(["true", "true"]);
  });

  test("in a grouped selection, members who all moved to the same new value are marked and the set starts unticked", async () => {
    mocks.previews = [{
      ...PREVIEWS[2]!,
      grouping: [{ field: "family_name", values: ["Smith", "Smith"], shown: "Smith" }],
    }];
    renderDialog(vi.fn(), [{
      members: ["joan", "joan-2"], heading: "Clarke", grouping: [{ field: "family_name", key: "Clarke" }],
    }]);

    const [set] = await screen.findAllByRole("checkbox");
    expect(set!.getAttribute("aria-checked")).toBe("false");
    expect(screen.getAllByText(message("duplicates.moved"))).toHaveLength(2);
    expect(mocks.variables).toContainEqual({ sets: [["joan", "joan-2"]], certain: false, groupings: [["family_name"]] });
  });

  test("an ungrouped selection and Review's certain pairs send no grouping and carry no mark", async () => {
    mocks.previews = [PREVIEWS[2]!];
    renderDialog(vi.fn(), [{ members: ["joan", "joan-2"] }], true);

    const [set] = await screen.findAllByRole("checkbox");
    expect(set!.getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText(message("duplicates.moved"))).toBeNull();
    expect(mocks.variables).toContainEqual({ sets: [["joan", "joan-2"]], certain: true, groupings: null });
  });

  describe("a confirmed run", () => {
    const clean = (number: number) => ({
      outcome: null, reason: "", digest: `digest-${number}`, survivor: { id: `p${number}` },
      members: [
        member(`p${number}`, `Person ${number}`, `p${number}@example.test`, "2020-01-01T00:00:00Z"),
        member(`q${number}`, `Person ${number}`, `p${number}@example.test`, "2021-01-01T00:00:00Z"),
      ],
      disagreements: [], fills: [], grouping: [],
    });
    const twelve = Array.from({ length: 12 }, (_value, index) => clean(index));
    const twelveSets = twelve.map((row) => ({ members: row.members.map((item) => item.id) }));
    const answer = (variables: { sets: string[][] }) => ({
      merge_duplicates: variables.sets.map((ids) => merged(ids[0]!, `Person ${ids[0]!.slice(1)}`)),
    });
    const deferred = () => {
      let resolve: (value: unknown) => void = () => undefined;
      const promise = new Promise((done) => { resolve = done; });
      return { promise, resolve };
    };

    test("sends the sets five at a time in order, shows its progress, and invalidates once", async () => {
      mocks.previews = twelve;
      mocks.merge.mockImplementation(async (variables: { sets: string[][] }) => answer(variables));
      renderDialog(vi.fn(), twelveSets);

      fireEvent.click(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "12" }) }));

      expect(await screen.findByText(message("duplicates.progress", { done: "12", total: "12" }))).toBeTruthy();
      expect(mocks.merge.mock.calls.map(([variables]) => (variables as { sets: string[][] }).sets.map((ids) => ids[0])))
        .toEqual([["p0", "p1", "p2", "p3", "p4"], ["p5", "p6", "p7", "p8", "p9"], ["p10", "p11"]]);
      expect(mocks.invalidate).toHaveBeenCalledTimes(1);
      // The resource lists behind (a list's rows and its total) refresh once too, each merged resource once.
      expect(mocks.invalidateResource.mock.calls.map(([params]) => (params as { resource: string }).resource))
        .toEqual([...PARTY_MERGE_RESOURCES]);
    });

    test("Stop ends the run after the chunk in flight and shows the rest as left out", async () => {
      mocks.previews = twelve;
      const first = deferred();
      mocks.merge.mockImplementationOnce(() => first.promise).mockImplementation(async (variables: { sets: string[][] }) => answer(variables));
      renderDialog(vi.fn(), twelveSets);
      fireEvent.click(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "12" }) }));

      fireEvent.click(await screen.findByRole("button", { name: message("duplicates.stop") }));
      first.resolve(answer({ sets: twelveSets.slice(0, 5).map((set) => [...set.members]) }));

      expect(await screen.findByText(message("duplicates.runStopped", { done: "5", total: "12" }))).toBeTruthy();
      expect(mocks.merge).toHaveBeenCalledTimes(1);
      expect(screen.getAllByText(message("duplicates.outcome.left"))).toHaveLength(7);
      expect(mocks.invalidate).toHaveBeenCalledTimes(1);
    });

    test("a failed request stops the run with what answered kept and nothing sent twice", async () => {
      mocks.previews = twelve;
      mocks.merge
        .mockImplementationOnce(async (variables: { sets: string[][] }) => answer(variables))
        .mockImplementationOnce(async () => { throw new Error("502"); });
      renderDialog(vi.fn(), twelveSets);

      fireEvent.click(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "12" }) }));

      expect(await screen.findByText(message("duplicates.runFailed", { done: "5", total: "12" }))).toBeTruthy();
      expect(mocks.merge).toHaveBeenCalledTimes(2);
      expect(screen.getAllByText(message("duplicates.outcome.left"))).toHaveLength(7);
    });

    test("closing mid-run asks first, and stopping there sends nothing more", async () => {
      mocks.previews = twelve;
      const first = deferred();
      mocks.merge.mockImplementationOnce(() => first.promise).mockImplementation(async (variables: { sets: string[][] }) => answer(variables));
      const { onClose } = renderDialog(vi.fn(), twelveSets);
      fireEvent.click(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "12" }) }));

      fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
      expect(await screen.findByText(message("duplicates.closeRunning"))).toBeTruthy();
      expect(onClose).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: message("duplicates.stopAndClose") }));
      first.resolve(answer({ sets: twelveSets.slice(0, 5).map((set) => [...set.members]) }));

      await waitFor(() => expect(onClose).toHaveBeenCalledWith(false));
      await waitFor(() => expect(mocks.invalidate).toHaveBeenCalledTimes(1));
      expect(mocks.merge).toHaveBeenCalledTimes(1);
    });
  });

  describe("a selection from an ungrouped list", () => {
    test("lists the duplicate sets the server found among it and says how many were left out", async () => {
      mocks.selection = { sets: [["joan", "joan-2"]], left_out: 44, explicit: false };
      mocks.previews = [PREVIEWS[2]!];
      renderDialog(vi.fn(), [], false, ["joan", "joan-2", ...Array.from({ length: 44 }, (_value, index) => `single-${index}`)]);

      expect(await screen.findByText(message("duplicates.leftOutUnpaired", { count: "44" }))).toBeTruthy();
      await waitFor(() => expect(mocks.variables).toContainEqual({ sets: [["joan", "joan-2"]], certain: false, groupings: null }));
      const [set] = await screen.findAllByRole("checkbox");
      expect(set!.getAttribute("aria-checked")).toBe("true");
    });

    test("says so when three to ten records with no pair among them are offered as one set", async () => {
      mocks.selection = { sets: [["joan", "joan-2"]], left_out: 0, explicit: true };
      mocks.previews = [PREVIEWS[2]!];
      renderDialog(vi.fn(), [], false, ["joan", "joan-2", "mary"]);

      expect(await screen.findByText(message("duplicates.explicitSet"))).toBeTruthy();
    });

    test("says what to do when no duplicate is found among more than ten", async () => {
      mocks.selection = { sets: [], left_out: 11, explicit: false };
      mocks.previews = [];
      renderDialog(vi.fn(), [], false, Array.from({ length: 11 }, (_value, index) => `single-${index}`));

      expect(await screen.findByText(message("duplicates.noneFound", { count: "11" }))).toBeTruthy();
      expect(screen.getByRole("button", { name: message("duplicates.confirm", { count: "0" }) })).toBeTruthy();
    });
  });

  describe("every certain pair from Review", () => {
    // Certain pair n is p<n> and q<n>, one address; every tenth one's records disagree on a name, so it is warned.
    const pair = (number: number) => ({
      id: `pair-${number}`, normalized_value: `p${number}@example.test`,
      left: { id: `p${number}`, display_name: `Person ${number}` },
      right: { id: `q${number}`, display_name: `Person ${number}` },
      survivor: { id: `p${number}` },
    });
    /** ``total`` certain pairs, fifty a page after the cursor (``Infinity`` never ends), one stable result a page. */
    const pages = (total: number) => {
      const cache = new Map<string | null, Read>();
      return (variables: { after: string | null }): Read => {
        if (!cache.has(variables.after)) {
          const from = variables.after === null ? 1 : Number(variables.after.slice("pair-".length)) + 1;
          const count = Math.max(0, Math.min(50, total - from + 1));
          cache.set(variables.after, {
            data: { certain_same_as_pairs: Array.from({ length: count }, (_value, index) => pair(from + index)) },
            isFetching: false, isFetchedAfterMount: true, error: null,
          });
        }
        return cache.get(variables.after)!;
      };
    };
    const pairReads = () => mocks.variables.filter((variables) => (variables as { limit?: number }).limit === 50);
    const confirmButton = () => screen.getByRole("button", { name: /^Merge \(/ });

    test("lists every pair page after page, then says the totals; one confirm runs every ticked set", async () => {
      mocks.certainRead = pages(120);
      mocks.previewRead = answering();
      mocks.merge.mockImplementation(async (variables: { sets: string[][] }) => ({
        merge_duplicates: variables.sets.map((ids) => merged(ids[0]!, `Person ${ids[0]!.slice(1)}`)),
      }));
      renderDialog(vi.fn(), [], false, undefined, { total: "120" });

      expect(await screen.findByText(message("duplicates.totals", { count: "120", merge: "108", look: "12" }))).toBeTruthy();
      expect(pairReads()).toEqual(expect.arrayContaining([
        { limit: 50, after: null }, { limit: 50, after: "pair-50" }, { limit: 50, after: "pair-100" },
      ]));
      expect(pairReads()).not.toContainEqual({ limit: 50, after: "pair-120" });
      // The shared pager shows fifty sets at a time.
      expect(screen.getAllByRole("checkbox")).toHaveLength(50);
      fireEvent.click(screen.getAllByRole("button", { name: "Next page" }).at(-1)!);
      expect(await screen.findByText(message("review.sameAs.certainHeading", {
        left: "Person 51", right: "Person 51", address: "p51@example.test",
      }))).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: message("duplicates.confirm", { count: "108" }) }));

      expect(await screen.findByText(message("duplicates.progress", { done: "108", total: "108" }))).toBeTruthy();
      expect(mocks.merge).toHaveBeenCalledTimes(22);
      expect(mocks.merge.mock.calls.every(([variables]) => (variables as { certain: boolean }).certain)).toBe(true);
    }, PAGED_RUN_TIMEOUT_MS);

    test("Confirm stays disabled while a page of previews is still loading", async () => {
      const answered = answering();
      mocks.certainRead = pages(120);
      mocks.previewRead = (variables) => (variables.sets[0]?.[0] === "p51"
        ? { data: undefined, isFetching: true, error: null }
        : answered(variables));
      renderDialog(vi.fn(), [], false, undefined, { total: "120" });

      expect(await screen.findByText(message("duplicates.loadedOf", { loaded: "50", total: "120" }))).toBeTruthy();
      expect(confirmButton().hasAttribute("disabled")).toBe(true);
    });

    test("a page that fails to load leaves Confirm disabled and offers a retry", async () => {
      const refetch = vi.fn();
      const answered = answering();
      mocks.certainRead = pages(120);
      mocks.previewRead = (variables) => (variables.sets[0]?.[0] === "p51"
        ? { data: undefined, isFetching: false, error: new Error("timeout"), refetch }
        : answered(variables));
      renderDialog(vi.fn(), [], false, undefined, { total: "120" });

      expect(await screen.findByText(message("duplicates.pageError"))).toBeTruthy();
      expect(confirmButton().hasAttribute("disabled")).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: message("duplicates.retry") }));
      expect(refetch).toHaveBeenCalledTimes(1);
    });

    test("a dialog opened again within the cache window lists from fresh reads only", async () => {
      // An earlier dialog left the first page in the cache: pairs merged since, still fresh for the app's stale time.
      const listed = pages(3);
      let reread = false;
      mocks.certainRead = (variables) => (variables.after === null && !reread
        ? {
          data: { certain_same_as_pairs: [pair(901), pair(902)] }, isFetching: false, isFetchedAfterMount: false,
          error: null, refetch: () => { reread = true; mocks.rerender?.(); },
        }
        : listed(variables));
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, { total: "3" });

      expect(await screen.findByText(message("duplicates.totals", { count: "3", merge: "3", look: "0" }))).toBeTruthy();
      expect(reread).toBe(true);
      expect(screen.queryByText(message("review.sameAs.certainHeading", {
        left: "Person 901", right: "Person 901", address: "p901@example.test",
      }))).toBeNull();
    });

    test("a cached page arriving before the fresh one is ignored", async () => {
      const listed = pages(60);
      let settled = false;
      mocks.certainRead = (variables) => {
        if (variables.after !== "pair-50" || settled) return listed(variables);
        // The page's old answer shows while its fresh read is in flight.
        setTimeout(() => { settled = true; mocks.rerender?.(); }, 20);
        return { data: { certain_same_as_pairs: [pair(950)] }, isFetching: true, isFetchedAfterMount: false, error: null };
      };
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, { total: "60" });

      // One wait per read: page one's previews, page two's cached answer, then what its fresh read lists.
      await waitFor(() => expect(mocks.variables.some((variables) =>
        (variables as { sets?: string[][] }).sets?.[0]?.[0] === "p1")).toBe(true));
      await waitFor(() => expect(pairReads()).toContainEqual({ limit: 50, after: "pair-50" }));
      await waitFor(() => expect(settled).toBe(true));
      expect(await screen.findByText(message("duplicates.totals", { count: "60", merge: "54", look: "6" }))).toBeTruthy();
      expect(screen.queryByText(message("review.sameAs.certainHeading", {
        left: "Person 950", right: "Person 950", address: "p950@example.test",
      }))).toBeNull();
    }, PAGED_RUN_TIMEOUT_MS);

    test("lists at most a thousand and says how to reach the rest", async () => {
      mocks.certainRead = pages(Infinity);
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, { total: "1,000+" });

      // One wait per read: twenty pages of pairs, then twenty of previews, outlast one shared wait under load.
      for (let page = 0; page < 20; page += 1) {
        await waitFor(() => expect(pairReads()).toContainEqual({ limit: 50, after: page === 0 ? null : `pair-${page * 50}` }));
      }
      for (let page = 0; page < 20; page += 1) {
        await waitFor(() => expect(mocks.variables.some((variables) =>
          (variables as { sets?: string[][] }).sets?.[0]?.[0] === `p${page * 50 + 1}`)).toBe(true));
      }
      expect(await screen.findByText(message("duplicates.firstOfAll", { count: "1,000", total: "1,000+" }))).toBeTruthy();
      expect(await screen.findByText(message("duplicates.totals", { count: "1,000", merge: "900", look: "100" })))
        .toBeTruthy();
      expect(pairReads()).toContainEqual({ limit: 50, after: "pair-950" });
      expect(pairReads()).not.toContainEqual({ limit: 50, after: "pair-1000" });
    }, PAGED_RUN_TIMEOUT_MS);
  });

  describe("every group a list matched", () => {
    // The People list grouped by name, repeated: its read as the list's own groups request sent it, order included.
    // Group n is "Person 000n" (the keys sort as their numbers), its records p<n> and q<n>.
    const read: AllMatchingGroups["read"] = {
      where: { display_name: { _is_null: false } },
      dimensions: [{ input: "DISPLAY_NAME", key: "display_name" }],
      having: { count_gt: 1 },
      orderBy: [{ field: "display_name", direction: "ASC", nulls: "LAST" }],
    };
    // Group 5055, a number no listing here reaches, sorts between groups 55 and 56.
    const keyOf = (number: number) => (number === 5055 ? "Person 0055a" : `Person ${String(number).padStart(4, "0")}`);
    const group = (number: number) => ({
      key: { display_name: keyOf(number), given_name: null, family_name: null },
      aggregate: { count: 2 },
      rows: [{ id: `p${number}` }, { id: `q${number}` }],
    });
    /** The key a page continues after: the `_gt` the dialog adds to the list's `where`, none on the first page. */
    const cursorOf = (variables: unknown): string | null => {
      const where = (variables as { where?: { _and?: readonly { display_name?: { _gt?: string } }[] } }).where;
      return where?._and?.at(-1)?.display_name?._gt ?? null;
    };
    /** The live groups in key order, a page past the cursor and offset, one stable result a page; ``shape`` may change one. */
    const groupPages = (live: number | (() => readonly number[]), shape: (number: number) => unknown = group) => {
      const cache = new Map<string, Read>();
      return (variables: { where?: unknown; limit: number; offset?: number }): Read => {
        const numbers = typeof live === "number" ? Array.from({ length: live }, (_value, index) => index + 1) : live();
        const after = cursorOf(variables);
        const offset = variables.offset ?? 0;
        const page = JSON.stringify([after, offset, numbers]);
        if (!cache.has(page)) {
          const listed = [...numbers].sort((left, right) => (keyOf(left) < keyOf(right) ? -1 : 1))
            .filter((number) => after === null || keyOf(number) > after).slice(offset, offset + variables.limit);
          cache.set(page, { data: { groups: listed.map(shape) }, isFetching: false, isFetchedAfterMount: true, error: null });
        }
        return cache.get(page)!;
      };
    };
    const groupsOf = (total: number) => ({ document: PeopleGroupSets, read, total, field: "display_name" });
    /** Each groups read the dialog sent, once, in the order sent. */
    const groupReads = () => [...new Map(mocks.variables
      .filter((variables) => (variables as { rows_limit?: number }).rows_limit === 11)
      .map((variables) => [JSON.stringify(variables), variables])).values()];
    /** The first record of every set a preview was asked for, once each. */
    const previewed = () => [...new Set(mocks.variables.flatMap((variables) =>
      (variables as { sets?: string[][] }).sets?.map(([first]) => first!) ?? []))];
    const confirmButton = () => screen.getByRole("button", { name: /^Merge \(/ });
    const mergedAll = async (variables: { sets: string[][] }) => ({
      merge_duplicates: variables.sets.map((ids) => merged(ids[0]!, `Person ${ids[0]!.slice(1)}`)),
    });

    test("lists every group page after page past the last key, in the list's order, then confirms in chunks", async () => {
      mocks.groupsRead = groupPages(120);
      mocks.previewRead = answering();
      mocks.merge.mockImplementation(mergedAll);
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(120));

      expect(await screen.findByText(message("duplicates.totals", { count: "120", merge: "108", look: "12" }))).toBeTruthy();
      expect(groupReads().map(cursorOf)).toEqual([null, keyOf(50), keyOf(100)]);
      // The list's own request on the first page; each next page its `where` narrowed past the last listed key.
      const wire = groupByVariables(read);
      expect(groupReads()[0]).toEqual({ ...groupByVariables({ ...read, pageSize: 50 }), rows_limit: 11 });
      for (const variables of groupReads()) {
        expect(variables).toMatchObject({ group_by: wire.group_by, having: wire.having, order_by: wire.order_by, offset: 0 });
      }
      expect(groupReads()[1]).toMatchObject({ where: { _and: [read.where, { display_name: { _gt: keyOf(50) } }] } });
      // Each set is one group, named by its value and grouped by the field, for the mark and the digest.
      expect(mocks.variables).toContainEqual(expect.objectContaining({
        groupings: Array.from({ length: 50 }, () => ["display_name"]),
      }));

      fireEvent.click(screen.getByRole("button", { name: message("duplicates.confirm", { count: "108" }) }));

      expect(await screen.findByText(message("duplicates.progress", { done: "108", total: "108" }))).toBeTruthy();
      expect(mocks.merge).toHaveBeenCalledTimes(22);
      expect(mocks.merge.mock.calls.every(([variables]) => {
        const sent = variables as { sets: string[][]; groupings: string[][] };
        return sent.groupings.length === sent.sets.length && sent.groupings.every((fields) => fields[0] === "display_name");
      })).toBe(true);
    }, PAGED_RUN_TIMEOUT_MS);

    test("a group added and a group removed between two pages is neither repeated nor skipped", async () => {
      let live: readonly number[] = Array.from({ length: 70 }, (_value, index) => index + 1);
      const listed = groupPages(() => live);
      let first: string | undefined;
      mocks.groupsRead = (variables) => {
        const request = JSON.stringify(variables);
        first ??= request;
        // Once the next page is asked for: group 10, listed, and group 60, not yet, leave; group 5055 arrives past the
        // last listed key. Paged by offset, the next page would start a group late and skip group 51.
        if (request !== first && !live.includes(5055)) {
          live = [...live.filter((number) => number !== 10 && number !== 60), 5055];
        }
        return listed(variables);
      };
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(70));

      expect(await screen.findByText(message("duplicates.totals", { count: "70", merge: "64", look: "6" }))).toBeTruthy();
      const expected = [...Array.from({ length: 70 }, (_value, index) => index + 1).filter((number) => number !== 60), 5055];
      expect([...previewed()].sort()).toEqual(expected.map((number) => `p${number}`).sort());
      expect(screen.queryByText(message("duplicates.listChanged", { count: "70" }))).toBeNull();
    }, PAGED_RUN_TIMEOUT_MS);

    test("a short page ends the listing though the list counted more, and says the list changed", async () => {
      mocks.groupsRead = groupPages(70);
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(120));

      expect(await screen.findByText(message("duplicates.listChanged", { count: "70" }))).toBeTruthy();
      expect(groupReads().map(cursorOf)).toEqual([null, keyOf(50)]);
      expect(screen.getByRole("button", { name: message("duplicates.confirm", { count: "63" }) })
        .hasAttribute("disabled")).toBe(false);
    }, PAGED_RUN_TIMEOUT_MS);

    test("a full page ending on a group without a key ends the listing, with no repeated request", async () => {
      // The fiftieth group, last on a full page, has no key: nulls sort last, so nothing follows it. Read as "from the
      // start" the next request would be the first page's again and the listing would never end.
      mocks.groupsRead = groupPages(50, (number) => (number === 50
        ? { ...group(50), key: { display_name: null, given_name: null, family_name: null } }
        : group(number)));
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(50));

      expect(await screen.findByText(message("duplicates.totals", { count: "49", merge: "45", look: "4" }))).toBeTruthy();
      expect(groupReads()).toHaveLength(1);
      expect(confirmButton().hasAttribute("disabled")).toBe(false);
    }, PAGED_RUN_TIMEOUT_MS);

    test("a full page ending on a key that is not text ends the listing and says the list changed", async () => {
      // No `_gt` cursor steps past a key that is not text: the listing ends there, though the list counted more.
      mocks.groupsRead = groupPages(120, (number) => (number === 50
        ? { ...group(50), key: { display_name: 42, given_name: null, family_name: null } }
        : group(number)));
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(120));

      expect(await screen.findByText(message("duplicates.listChanged", { count: "50" }))).toBeTruthy();
      expect(groupReads()).toHaveLength(1);
      expect(screen.getByText(message("duplicates.totals", { count: "49", merge: "45", look: "4" }))).toBeTruthy();
    }, PAGED_RUN_TIMEOUT_MS);

    test("a paged dialog says it is loading from its first render, before any page has answered", async () => {
      // Nothing has answered and nothing reports itself fetching: the dialog still says how far it has read.
      mocks.groupsRead = () => ({ data: undefined, isFetching: false, error: null });
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(120));

      expect(await screen.findByText(message("duplicates.loadedGroupsOf", { loaded: "0", total: "120" }))).toBeTruthy();
      expect(screen.getByText(message("duplicates.loading"))).toBeTruthy();
      cleanup();

      mocks.certainRead = () => ({ data: undefined, isFetching: false, error: null });
      renderDialog(vi.fn(), [], false, undefined, { total: "120" });

      expect(await screen.findByText(message("duplicates.loadedOf", { loaded: "0", total: "120" }))).toBeTruthy();
      expect(screen.getByText(message("duplicates.loading"))).toBeTruthy();
    });

    test("Confirm stays disabled until the last page of groups is listed", async () => {
      const listed = groupPages(120);
      mocks.groupsRead = (variables) => (cursorOf(variables) === keyOf(50)
        ? { data: undefined, isFetching: true, error: null }
        : listed(variables));
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(120));

      expect(await screen.findByText(message("duplicates.loadedGroupsOf", { loaded: "50", total: "120" }))).toBeTruthy();
      expect(confirmButton().hasAttribute("disabled")).toBe(true);
    });

    test("a group that changed since it was listed is refused by its digest, the rest merge", async () => {
      mocks.groupsRead = groupPages(3);
      mocks.previewRead = answering();
      const changedReason = "These records changed since you saw them; look at them again.";
      mocks.merge.mockImplementation(async (variables: { sets: string[][] }) => ({
        merge_duplicates: variables.sets.map((ids) => (ids[0] === "p2"
          ? { members: ids, outcome: "REFUSED", reason: changedReason, survivor: null }
          : merged(ids[0]!, `Person ${ids[0]!.slice(1)}`))),
      }));
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(3));

      fireEvent.click(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "3" }) }));

      expect(await screen.findByText(message("duplicates.outcome.refused", { reason: changedReason }))).toBeTruthy();
      expect(mocks.merge.mock.calls[0]![0]).toMatchObject({ digests: ["digest-1", "digest-2", "digest-3"] });
    });

    test("lists at most a thousand groups and says how to reach the rest", async () => {
      mocks.groupsRead = groupPages(1200);
      mocks.previewRead = answering();
      renderDialog(vi.fn(), [], false, undefined, undefined, groupsOf(1200));

      // One wait per read: twenty pages of groups, then twenty of previews.
      for (let page = 0; page < 20; page += 1) {
        await waitFor(() => expect(groupReads().map(cursorOf)).toContain(page === 0 ? null : keyOf(page * 50)));
      }
      for (let page = 0; page < 20; page += 1) {
        await waitFor(() => expect(previewed()).toContain(`p${page * 50 + 1}`));
      }
      expect(await screen.findByText(message("duplicates.firstOfGroups", { count: "1,000", total: "1,200" }))).toBeTruthy();
      expect(groupReads().map(cursorOf)).not.toContain(keyOf(1000));
    }, PAGED_RUN_TIMEOUT_MS);

    test("a group of one or without a value is never a set, and is counted", async () => {
      mocks.groupsRead = groupPages(5, (number) => (number === 1
        ? { ...group(1), aggregate: { count: 1 }, rows: [{ id: "p1" }] }
        : number === 2 ? { ...group(2), key: { display_name: "", given_name: null, family_name: null } } : group(number)));
      mocks.previewRead = answering();
      // A level that is not "repeated" reads its groups of one too: the dialog skips them.
      const flat = { where: read.where, dimensions: read.dimensions, orderBy: read.orderBy };
      renderDialog(vi.fn(), [], false, undefined, undefined, { ...groupsOf(5), read: flat });

      expect(await screen.findByText(message("duplicates.unpairedAll", { count: "1" }))).toBeTruthy();
      expect(screen.getByText(message("duplicates.blank", { count: "2" }))).toBeTruthy();
      expect(await screen.findByRole("button", { name: message("duplicates.confirm", { count: "3" }) })).toBeTruthy();
    });
  });
});
