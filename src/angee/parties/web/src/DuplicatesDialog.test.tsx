// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, retainSearchParams, useRouter,
} from "@tanstack/react-router";
import * as React from "react";
import { AppRuntimeProvider, InAppLinkProvider, createRouteHref, routerNavigator } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  merge: vi.fn(), invalidate: vi.fn(), invalidateResource: vi.fn(), previews: [] as readonly unknown[],
  variables: [] as unknown[], selection: undefined as unknown,
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
  useAuthoredQuery: (_document: unknown, variables: unknown) => {
    mocks.variables.push(variables);
    return {
      data: { duplicate_set_previews: mocks.previews, selection_duplicate_sets: mocks.selection },
      isFetching: false, error: null,
    };
  },
  useAuthoredMutation: () => [mocks.merge, { fetching: false, error: null, reset: () => undefined }],
  useInvalidateAuthoredModels: () => mocks.invalidate,
}));

import { DuplicatesDialog, type DuplicateSetInput } from "./DuplicatesDialog";
import { PARTY_MERGE_RESOURCES } from "./documents";
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
function renderDialog(onClose = vi.fn(), sets = SETS, certain = false, selection?: readonly string[]) {
  function Host(): React.ReactElement {
    const router = useRouter();
    const navigate = React.useMemo(() => routerNavigator(router), [router]);
    return (
      <InAppLinkProvider navigate={navigate}>
        <DuplicatesDialog sets={sets} selection={selection} certain={certain} onClose={onClose} />
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
});

