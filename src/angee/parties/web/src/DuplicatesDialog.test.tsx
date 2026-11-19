// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, retainSearchParams, useRouter,
} from "@tanstack/react-router";
import * as React from "react";
import { AppRuntimeProvider, InAppLinkProvider, createRouteHref, routerNavigator } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ merge: vi.fn(), previews: [] as readonly unknown[] }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: { duplicate_set_previews: mocks.previews }, isFetching: false, error: null }),
  useAuthoredMutation: () => [mocks.merge, { fetching: false, error: null, reset: () => undefined }],
}));

import { DuplicatesDialog, type DuplicateSetInput } from "./DuplicatesDialog";
import { enPartiesMessages } from "./i18n";
import parties from "./index";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties-duplicates" });
beforeEach(() => {
  mocks.merge.mockReset();
  mocks.previews = PREVIEWS;
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
function renderDialog(onClose = vi.fn(), sets = SETS, certain = false) {
  function Host(): React.ReactElement {
    const router = useRouter();
    const navigate = React.useMemo(() => routerNavigator(router), [router]);
    return (
      <InAppLinkProvider navigate={navigate}>
        <DuplicatesDialog sets={sets} certain={certain} onClose={onClose} />
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
    expect(mocks.merge).toHaveBeenCalledWith({ sets: [["joan", "joan-2"]], digests: ["digest-joan"], certain: false });
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
      sets: [["ada", "augusta"]], digests: ["digest-ada"], certain: false,
    }));
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
      sets: [["grace", "amazing-grace"]], digests: ["digest-grace"], certain: false,
    }));
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
});
