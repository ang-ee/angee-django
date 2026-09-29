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
  type AppRuntime,
  type ChatterViewContext,
} from "../runtime";
import { Chatter, useChatterPresentation } from "./Chatter";
import { ChatterProvider, useChatterContent, type ChatterContent } from "./chatter-context";
import { useRecordPeek } from "./record-peek";
import { registerForm, type RegisteredFormProps } from "../views/form/registered-form";

afterEach(() => cleanup());

describe("Chatter", () => {
  test("route tabs admit defaults and contributions before rendering or counting", async () => {
    const excludedRender = vi.fn(() => <span>Excluded content</span>);
    const excludedCount = vi.fn(() => 9);
    renderChatter({ chatterRoutes: [{ name: "notes.record", path: "/records/$id", viewType: "notes/note", recordParam: "id", chatter: { tabs: ["comments"] } }], chatter: [
      { id: "agents", label: "Agents", useCount: excludedCount, render: excludedRender },
    ] });
    expect(await screen.findByRole("tab", { name: "Comments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Activity" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Agents" })).toBeNull();
    expect(excludedRender).not.toHaveBeenCalled();
    expect(excludedCount).not.toHaveBeenCalled();
  });

  test("route selection can admit a conditional tab", async () => {
    const hiddenRender = vi.fn(() => <span>Not yet visible</span>);
    renderChatter({
      chatterRoutes: [{ name: "notes.record", path: "/records/$id", viewType: "notes/record", recordParam: "id", chatter: { tabs: ["activity", "conditional"] } }],
      chatter: [{ id: "conditional", when: () => false, render: hiddenRender }],
    });
    expect(await screen.findByRole("tab", { name: "Activity" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
    expect(hiddenRender).not.toHaveBeenCalled();
  });

  test("an empty route tab list removes the aside, including published tabs", () => {
    render(chatterContentView(<PublishedContent content={{ tabs: [{ id: "local", label: "Local", children: <span>Local content</span> }] }} />,
      "local", { chatterRoutes: [{ name: "notes.home", path: "/", viewType: "notes/home", chatter: { tabs: [] } }] }));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  test("unknown route tabs fail before a contribution's render or count hook", () => {
    const excludedRender = vi.fn(() => null);
    const excludedCount = vi.fn(() => 0);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => render(chatterContentView(null, "comments", {
        chatterRoutes: [{ name: "notes.home", path: "/", viewType: "notes/home", chatter: { tabs: ["typo"] } }],
        chatter: [{ id: "known", render: excludedRender, useCount: excludedCount }],
      }))).toThrow('unknown contribution id "typo"');
      expect(excludedRender).not.toHaveBeenCalled();
      expect(excludedCount).not.toHaveBeenCalled();
    } finally { consoleError.mockRestore(); }
  });

  test("only renders the agents tab when an addon contributes it", async () => {
    renderChatter({});

    expect(await screen.findByRole("tab", { name: "Comments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Agents" })).toBeNull();

    cleanup();
    renderChatter({
      chatter: [{
        id: "agents",
        label: "Agents",
        icon: "agent",
        render: () => <span>Agents-owned empty state</span>,
      }],
    });

    expect(await screen.findByRole("tab", { name: "Agents" })).toBeTruthy();
    expect(screen.getByText("Agents-owned empty state")).toBeTruthy();
  });

  test("prefers reactive contribution counts while preserving static counts", async () => {
    renderChatter({
      chatterRoutes: [
        {
          name: "notes.record",
          path: "/records/$id",
          viewType: "notes/record",
          modelLabel: "notes.Note",
          recordParam: "id",
        },
      ],
      chatter: [
        {
          id: "comments",
          label: "Comments",
          icon: "comments",
          count: 2,
          useCount: useCommentsCount,
          render: (context) => <span>{context.view.sqid}</span>,
        },
        {
          id: "activity",
          label: "Activity",
          icon: "activity",
          count: 5,
          render: () => <span>Activity panel</span>,
        },
      ],
    });

    expect(await screen.findByRole("tab", { name: /Activity\s*5/ })).toBeTruthy();
    const commentsTab = await screen.findByRole("tab", {
      name: /Comments\s*7/,
    });
    expect(commentsTab.textContent).not.toContain("2");
  });

  test("scopes before rendering while canonical models include MTI subtypes", async () => {
    const hiddenCount = vi.fn(() => 9);
    const hiddenRender = vi.fn(() => <span>Wrong model</span>);
    const blockedRender = vi.fn(() => <span>Blocked</span>);
    renderChatter({
      chatterRoutes: [
        {
          name: "notes.record",
          path: "/records/$id",
          viewType: "notes/record",
          modelLabel: "crm.Vip",
          canonicalLabel: "parties.Party",
          recordParam: "id",
        },
      ],
      chatter: [
        {
          id: "wrong-model",
          model: "notes.Note",
          label: "Wrong model",
          useCount: hiddenCount,
          render: hiddenRender,
        },
        {
          id: "blocked",
          model: "parties.Party",
          when: () => false,
          label: "Blocked",
          render: blockedRender,
        },
        {
          id: "history",
          model: "parties.Party",
          when: (context) => context.view.kind === "record",
          label: "History",
          render: (context) => <span>History for {context.view.sqid}</span>,
        },
      ],
    });

    const historyTab = await screen.findByRole("tab", { name: "History" });
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

function renderChatter(runtime: Partial<AppRuntime>, record = true): void {
  function VisibilityProbe() { return <span>{useChatterPresentation().visible ? "Aside available" : "Aside hidden"}</span>; }
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const recordRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: record ? "/records/$id" : "/records",
    component: () => (
      <AppRuntimeProvider runtime={{ icons: baseIcons, chatterRoutes: [{ name: "notes.record", path: "/records/$id", viewType: "notes/note", modelLabel: "notes.Note", recordParam: "id" }], ...runtime }}>
        <ChatterProvider defaultTab="agents">
          <VisibilityProbe />
          <Chatter />
        </ChatterProvider>
      </AppRuntimeProvider>
    ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([recordRoute]),
    history: createMemoryHistory({ initialEntries: [record ? "/records/rec_1" : "/records"] }),
  });

  render(<RouterProvider router={router} />);
}

function chatterContentView(children: React.ReactNode, defaultTab: string, runtime: Partial<AppRuntime> = {}): React.ReactElement {
  return (
    <RouterContextProvider router={createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) })}>
      <AppRuntimeProvider runtime={{ icons: baseIcons, ...runtime }}>
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


test("non-record pages hide empty chatter and admit a contributed inbox", async () => {
  renderChatter({}, false);
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Activity" })).toBeNull();
  cleanup();
  renderChatter({ chatter: [{ id: "inbox", label: "Inbox", render: () => <span>Inbox content</span> }] }, false);
  expect(await screen.findByRole("tab", { name: "Inbox" })).toBeTruthy();
  expect(screen.getByText("Aside available")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
});

test("a hidden record route suppresses the aside before contributions execute", async () => {
  const renderTab = vi.fn(() => <span>Private panel</span>);
  renderChatter({
    chatterRoutes: [{ name: "notes.record", path: "/records/$id", viewType: "notes/note", modelLabel: "notes.Note", recordParam: "id", chatter: "hidden" }],
    chatter: [{ id: "extra", render: renderTab }],
  }, true);
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(renderTab).not.toHaveBeenCalled();
});

test("a route can explicitly opt a non-record page into default chatter", async () => {
  renderChatter({ chatterRoutes: [{ name: "notes.all", path: "/records", viewType: "notes/note", chatter: { tabs: ["comments", "activity"] } }] }, false);
  expect(await screen.findByText("Aside available")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Comments" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Activity" })).toBeTruthy();
});

test("record and non-record defaults follow the route", async () => {
  renderChatter({}, false);
  expect(await screen.findByText("Aside hidden")).toBeTruthy();
  cleanup();
  renderChatter({}, true);
  expect(await screen.findByText("Aside available")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Comments" })).toBeTruthy();
});
