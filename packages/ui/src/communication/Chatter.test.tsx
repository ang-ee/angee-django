// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet,
  RouterContextProvider,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { baseIcons } from "../chrome/icon-registry";
import {
  AppRuntimeProvider,
  containersFromChildren,
  type AppRuntime,
  type ChatterRoute,
  type ChatterTabContent,
  type ChatterViewContext,
  type ComposedContainers,
  type ContainerChild,
  type ContainerRule,
} from "../runtime";
import { Chatter, useChatterPresentation } from "./Chatter";
import { CHATTER_CONTAINERS } from "./chatter-containers";
import { ChatterProvider, useChatter, useChatterContent, type ChatterContent } from "./chatter-context";
import { useRecordPeek } from "./record-peek";
import { registerForm, type RegisteredFormProps } from "../views/form/registered-form";

afterEach(() => cleanup());

const NOTE_RECORD: ChatterRoute = { name: "notes.record", path: "/records/$id", viewType: "notes/note", modelLabel: "notes.Note", recordParam: "id" };
const NOTE_LIST: ChatterRoute = { name: "notes.all", path: "/records", viewType: "notes/note", modelLabel: "notes.Note" };

type Tabs = Readonly<Record<string, ContainerChild<ChatterTabContent>>>;

/**
 * Addon-owned example tabs plus extra children and per-layer narrowing rules.
 */
function aside(
  children: Readonly<Record<string, Tabs>> = {},
  { rules = [], examples = true }: { rules?: (Omit<ContainerRule, "exempt" | "rank"> & { rank?: number })[]; examples?: boolean } = {},
): ComposedContainers {
  const base = containersFromChildren(CHATTER_CONTAINERS, examples ? {
    "record#aside": {
      "notes.comments": { sequence: 10, content: { label: "Comments", render: () => <span>Comments content</span> } },
      "notes.history": { sequence: 20, content: { label: "History", render: () => <span>History content</span> } },
    },
  } : {});
  const added = containersFromChildren([], children);
  return {
    ...base,
    children: Object.fromEntries([...new Set([...Object.keys(base.children), ...Object.keys(added.children)])]
      .map((address) => [address, [...(base.children[address] ?? []), ...(added.children[address] ?? [])]])),
    // Rules rank in the order given unless a test ranks them.
    rules: rules.length ? { "record#aside": rules.map((rule, index) => ({ rank: index, ...rule, exempt: [] })) } : {},
  };
}

const tab = (content: ChatterTabContent, sequence?: number): ContainerChild<ChatterTabContent> =>
  ({ content, ...(sequence !== undefined ? { sequence } : {}) });

const onRoute = (...routes: string[]) => ({ apps: [], routes });

describe("Chatter", () => {
  test("the framework contributes no placeholder tabs", async () => {
    renderChatter({ containers: aside({}, { examples: false }) });
    expect(await screen.findByText("Aside hidden")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  test("a layer's only on the route narrows the tabs before they render or count", async () => {
    const excludedRender = vi.fn(() => <span>Excluded content</span>);
    const excludedCount = vi.fn(() => 9);
    renderChatter({
      containers: aside(
        { "record#aside": { "agents.chat": tab({ label: "Agents", useCount: excludedCount, render: excludedRender }) } },
        { rules: [{ layer: "notes", when: { route: "notes.record" }, only: ["notes.comments"] }] },
      ),
      containerScope: onRoute("notes.record"),
    });
    expect(await screen.findByRole("tab", { name: "Comments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "History" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Agents" })).toBeNull();
    expect(excludedRender).not.toHaveBeenCalled();
    expect(excludedCount).not.toHaveBeenCalled();
  });

  test("narrowing keeps a tab whose own when still decides", async () => {
    const hiddenRender = vi.fn(() => <span>Not yet visible</span>);
    renderChatter({
      containers: aside(
        { "record#aside": { "notes.conditional": tab({ label: "Conditional", when: () => false, render: hiddenRender }) } },
        { rules: [{ layer: "notes", only: ["notes.history", "notes.conditional"] }] },
      ),
    });
    expect(await screen.findByRole("tab", { name: "History" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Conditional" })).toBeNull();
    expect(hiddenRender).not.toHaveBeenCalled();
  });

  test("a narrowing applies only where its condition holds", async () => {
    const rules = [{ layer: "notes", when: { route: "notes.board" }, only: [] }];
    renderChatter({ containers: aside({}, { rules }), containerScope: onRoute("notes.record") });
    expect(await screen.findByText("Aside available")).toBeTruthy();
    cleanup();
    renderChatter({ containers: aside({}, { rules }), containerScope: onRoute("notes.board.card", "notes.board") });
    expect(await screen.findByText("Aside hidden")).toBeTruthy();
  });

  test("an empty only removes the record aside", async () => {
    renderChatter({ containers: aside({}, { rules: [{ layer: "notes", only: [] }] }) });
    expect(await screen.findByText("Aside hidden")).toBeTruthy();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  test("an aside whose every tab a layer excepts is absent", async () => {
    renderChatter({ containers: aside({}, { rules: [{ layer: "notes", except: ["notes.comments", "notes.history"] }] }) });
    expect(await screen.findByText("Aside hidden")).toBeTruthy();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  test("a hidden tab leaves its siblings; a later layer shows it again", async () => {
    renderChatter({ containers: aside({}, { rules: [{ layer: "notes", hide: ["notes.history"] }] }) });
    expect(await screen.findByRole("tab", { name: "Comments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "History" })).toBeNull();
    cleanup();
    renderChatter({ containers: aside({}, { rules: [{ layer: "notes", hide: ["notes.history"] }, { layer: "product", show: ["notes.history"] }] }) });
    expect(await screen.findByRole("tab", { name: "History" })).toBeTruthy();
  });

  test("page-published tabs show the aside on their own and take the same narrowing", async () => {
    const published = <PublishedContent content={{ tabs: [{ id: "local", label: "Local", children: <span>Local content</span> }] }} />;
    render(chatterContentView(published, "local", { containers: aside() }));
    expect(await screen.findByRole("tab", { name: "Local" })).toBeTruthy();
    expect(screen.getByText("Local content")).toBeTruthy();
    // Not a record view: the examples's record-level tabs stay off.
    expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
    cleanup();
    const rules = [{ layer: "notes", when: { route: "notes.home" }, only: [] }];
    render(chatterContentView(published, "local", { containers: aside({}, { rules }), containerScope: onRoute("notes.home") }));
    expect(screen.queryByRole("complementary")).toBeNull();
    cleanup();
    render(chatterContentView(published, "local", { containers: aside({}, { rules: [{ layer: "notes", hide: ["local"] }] }) }));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  test("a chatterTab link names a tab by its id", async () => {
    renderChatter({ containers: aside() }, { search: "?chatterTab=notes.history" });
    const selected = await screen.findByRole("tab", { name: "History" });
    await waitFor(() => expect(selected.getAttribute("aria-selected")).toBe("true"));
  });

  test("only renders the agents tab when an addon contributes it", async () => {
    renderChatter({ containers: aside() });

    expect(await screen.findByRole("tab", { name: "Comments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Agents" })).toBeNull();

    cleanup();
    renderChatter({
      containers: aside({ "record#aside": { "agents.chat": tab({
        label: "Agents",
        icon: "agent",
        render: () => <span>Agents-owned empty state</span>,
      }) } }),
    });

    fireEvent.click(await screen.findByRole("tab", { name: "Agents" }));
    expect(await screen.findByText("Agents-owned empty state")).toBeTruthy();
  });

  test("prefers reactive contribution counts while preserving static counts", async () => {
    renderChatter({
      containers: aside({ "record#aside": {
        "messaging.comments": tab({
          label: "Comments",
          icon: "comments",
          count: 2,
          useCount: useCommentsCount,
          render: (context) => <span>{context.view.sqid}</span>,
        }, 10),
        "messaging.history": tab({
          label: "History",
          icon: "activity",
          count: 5,
          render: () => <span>History panel</span>,
        }, 20),
      } }, { examples: false }),
    });

    expect(await screen.findByRole("tab", { name: /History\s*5/ })).toBeTruthy();
    const commentsTab = await screen.findByRole("tab", {
      name: /Comments\s*7/,
    });
    expect(commentsTab.textContent).not.toContain("2");
  });

  test("model tabs follow the record's canonical and concrete models, scoped before rendering", async () => {
    const hiddenCount = vi.fn(() => 9);
    const hiddenRender = vi.fn(() => <span>Wrong model</span>);
    const blockedRender = vi.fn(() => <span>Blocked</span>);
    renderChatter({
      chatterRoutes: [{ name: "crm.vip", path: "/records/$id", viewType: "crm/vip", modelLabel: "crm.Vip", canonicalLabel: "parties.Party", recordParam: "id" }],
      containers: aside({
        "notes.Note#aside": { "notes.wrong": tab({ label: "Wrong model", useCount: hiddenCount, render: hiddenRender }) },
        "parties.Party#aside": {
          "parties.blocked": tab({ label: "Blocked", when: () => false, render: blockedRender }),
          "parties.history": tab({
            label: "History",
            when: (context) => context.view.kind === "record",
            render: (context) => <span>History for {context.view.sqid}</span>,
          }),
        },
        "crm.Vip#aside": { "crm.perks": tab({ label: "Perks", render: () => <span>Perks</span> }) },
      }, { examples: false }),
    });

    const historyTab = await screen.findByRole("tab", { name: "History" });
    // Unsequenced siblings across the model addresses resolve in id order.
    expect(screen.getAllByRole("tab").map((node) => node.textContent)).toEqual(["Perks", "History"]);
    fireEvent.click(historyTab);
    expect(screen.getByText("History for rec_1")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Wrong model" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Blocked" })).toBeNull();
    expect(hiddenCount).not.toHaveBeenCalled();
    expect(hiddenRender).not.toHaveBeenCalled();
    expect(blockedRender).not.toHaveBeenCalled();
  });

  test("lazily opens record peeks without discarding an unsaved workflow input", async () => {
    const recordsMounted = vi.fn();
    renderChatterContent(
      <PublishedContent content={{
        tabs: [
          { id: "workflow", label: "Workflow", children: <label>Reason<input aria-label="Reason" defaultValue="" /></label> },
          { id: "records", label: "Records", children: <MountProbe onMount={recordsMounted}>Document evidence</MountProbe> },
        ],
      }} />,
      "workflow",
    );

    const input = await screen.findByRole("textbox", { name: "Reason" });
    fireEvent.change(input, { target: { value: "keep this draft" } });
    expect(recordsMounted).not.toHaveBeenCalled();
    expect(screen.queryByText("Document evidence")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    expect(await screen.findByText("Document evidence")).toBeTruthy();
    expect(recordsMounted).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("tab", { name: "Workflow" }));
    expect((screen.getByRole("textbox", { name: "Reason" }) as HTMLInputElement).value).toBe("keep this draft");
    expect(recordsMounted).toHaveBeenCalledOnce();
  });

  test("lets active panel content shrink to the chatter viewport", async () => {
    renderChatterContent(
      <PublishedContent content={{
        tabs: [{ id: "workflow", label: "Workflow", children: <span>Compact decision</span> }],
      }} />,
      "workflow",
    );

    const panelContent = (await screen.findByText("Compact decision"))
      .closest<HTMLElement>('[role="presentation"]');
    expect(panelContent).not.toBeNull();
    expect(panelContent?.style.minWidth).toBe("0");
    expect(panelContent?.className).toContain("w-full");
  });

  test("removing a temporary record peek preserves the publisher's tabs and composer", async () => {
    const base = {
      tabs: [{ id: "workflow", label: "Workflow", children: "Decision form" }],
      composer: <button type="button">Add comment</button>,
    } satisfies ChatterContent;
    const peek = {
      tabs: [{ id: "records", label: "Records", children: "Document evidence" }],
    } satisfies ChatterContent;
    renderChatterContent(
      <CompositionHarness base={base} peek={peek} />,
      "workflow",
    );

    expect(await screen.findByRole("tab", { name: "Workflow" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Records" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add comment" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Close record peek" }));
    await waitFor(() => expect(screen.queryByRole("tab", { name: "Records" })).toBeNull());
    expect(screen.getByRole("tab", { name: "Workflow" })).toBeTruthy();
    expect(screen.getByText("Decision form")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add comment" })).toBeTruthy();
  });

  test("record peeks render the registered canonical form in read-only mode", async () => {
    const seen = vi.fn();
    const CanonicalForm = (props: RegisteredFormProps) => {
      seen(props);
      return <p>Canonical counterparty details</p>;
    };
    render(chatterContentView(<RecordPeekHarness />, "comments", {
      forms: { "parties.Party": registerForm("parties.Party", CanonicalForm) },
    }));
    fireEvent.change(screen.getByRole("textbox", { name: "Review note" }), {
      target: { value: "Retain this review" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Inspect counterparty" }));
    expect(await screen.findByText("Canonical counterparty details")).toBeTruthy();
    expect(seen).toHaveBeenCalledWith(expect.objectContaining({
      resource: "parties.Party", id: "pty_1", readOnly: true, hideRecordChrome: true,
    }));
    expect((screen.getByRole("textbox", { name: "Review note" }) as HTMLInputElement).value)
      .toBe("Retain this review");
  });

  test("initial record evidence selects Records before explicit tab intent", async () => {
    renderRecordPeekIntent();

    fireEvent.click(screen.getByRole("button", { name: "Load initial evidence" }));
    const records = await screen.findByRole("tab", { name: "Records" });
    expect(records.getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("Evidence pty_1")).toBeTruthy();
  });

  test("late initial record evidence preserves explicit tab intent", async () => {
    renderRecordPeekIntent();

    fireEvent.click(screen.getByRole("tab", { name: "Workflow" }));
    expect(screen.getByRole("tab", { name: "Workflow" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Load initial evidence" }));
    await screen.findByRole("tab", { name: "Records" });
    expect(screen.getByRole("tab", { name: "Workflow" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Workflow history")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open evidence" }));
    expect(screen.getByRole("tab", { name: "Records" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("Evidence pty_2")).toBeTruthy();
  });
});

function RecordPeekHarness(): React.ReactElement {
  const open = useRecordPeek();
  return <>
    <input aria-label="Review note" />
    <button type="button" onClick={() => open({ model: "parties.Party", id: "pty_1" })}>
      Inspect counterparty
    </button>
  </>;
}

function RecordPeekIntentHarness(): React.ReactElement {
  const open = useRecordPeek();
  return <>
    <button type="button" onClick={() => open(
      { model: "parties.Party", id: "pty_1" },
      { tabActivation: "initial" },
    )}>
      Load initial evidence
    </button>
    <button type="button" onClick={() => open({ model: "parties.Party", id: "pty_2" })}>
      Open evidence
    </button>
  </>;
}

function renderRecordPeekIntent(): void {
  const CanonicalForm = ({ id }: RegisteredFormProps) => <p>Evidence {id}</p>;
  render(chatterContentView(<>
    <PublishedContent content={{ tabs: [
      { id: "workflow", label: "Workflow", children: <p>Workflow history</p> },
    ] }} />
    <RecordPeekIntentHarness />
  </>, "comments", {
    forms: { "parties.Party": registerForm("parties.Party", CanonicalForm) },
  }));
}

function PublishedContent({ content }: { content: ChatterContent }): null {
  useChatterContent(content);
  return null;
}

function CompositionHarness({ base, peek }: { base: ChatterContent; peek: ChatterContent }): React.ReactElement {
  const [open, setOpen] = React.useState(true);
  return <>
    <PublishedContent content={base} />
    {open ? <PublishedContent content={peek} /> : null}
    <button type="button" onClick={() => setOpen(false)}>Close record peek</button>
  </>;
}

function MountProbe({ onMount, children }: { onMount: () => void; children: React.ReactNode }): React.ReactElement {
  React.useEffect(() => onMount(), [onMount]);
  return <>{children}</>;
}

function useCommentsCount(
  context: ChatterViewContext,
): number | undefined {
  if (context.route?.modelLabel !== "notes.Note") return undefined;
  if (context.view.kind !== "record") return undefined;
  return context.view.sqid === "rec_1" ? 7 : undefined;
}

type TestRuntime = Partial<AppRuntime>;

function renderChatter(
  runtime: TestRuntime,
  { record = true, search = "", defaultTab = "agents", children }: { record?: boolean; search?: string; defaultTab?: string; children?: React.ReactNode } = {},
): void {
  function VisibilityProbe() { return <span>{useChatterPresentation().visible ? "Aside available" : "Aside hidden"}</span>; }
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const recordRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: record ? "/records/$id" : "/records",
    component: () => (
      <AppRuntimeProvider runtime={{ icons: baseIcons, chatterRoutes: [record ? NOTE_RECORD : NOTE_LIST], ...runtime }}>
        <ChatterProvider defaultTab={defaultTab}>
          <VisibilityProbe />
          {children}
          <Chatter />
        </ChatterProvider>
      </AppRuntimeProvider>
    ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([recordRoute]),
    history: createMemoryHistory({ initialEntries: [`${record ? "/records/rec_1" : "/records"}${search}`] }),
  });

  render(<RouterProvider router={router} />);
}

function chatterContentView(children: React.ReactNode, defaultTab: string, runtime: TestRuntime = {}): React.ReactElement {
  return (
    <RouterContextProvider router={createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) })}>
      <AppRuntimeProvider runtime={{ icons: baseIcons, containers: aside(), ...runtime }}>
        <ChatterProvider defaultTab={defaultTab}>
          {children}
          <Chatter />
        </ChatterProvider>
      </AppRuntimeProvider>
    </RouterContextProvider>
  );
}

function renderChatterContent(children: React.ReactNode, defaultTab: string) {
  return render(chatterContentView(children, defaultTab));
}

test("record-level tabs stay off pages without a record", async () => {
  renderChatter({ containers: aside() }, { record: false });
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "History" })).toBeNull();
  cleanup();
  renderChatter({ containers: aside({ "record#aside": { "inbox.inbox": tab({ label: "Inbox", render: () => <span>Inbox content</span> }) } }) }, { record: false });
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Inbox" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
});

test("a route hiding the aside suppresses it before contributions execute", async () => {
  const renderTab = vi.fn(() => <span>Private panel</span>);
  renderChatter({
    containers: aside(
      { "record#aside": { "notes.extra": tab({ render: renderTab }) } },
      { rules: [{ layer: "notes", when: { route: "notes.record" }, only: [] }] },
    ),
    containerScope: onRoute("notes.record"),
  });
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(renderTab).not.toHaveBeenCalled();
});

test("a model's own tabs show on its collection page", async () => {
  renderChatter({ containers: aside({ "notes.Note#aside": { "notes.digest": tab({ label: "Digest", render: () => <span>Digest</span> }) } }) }, { record: false });
  expect(await screen.findByText("Aside available")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Digest" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
});

test("record and non-record defaults follow the route", async () => {
  renderChatter({ containers: aside() }, { record: false });
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  cleanup();
  renderChatter({ containers: aside() });
  expect(await screen.findByText("Aside available")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Comments" })).toBeTruthy();
});
