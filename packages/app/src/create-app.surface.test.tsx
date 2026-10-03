// @vitest-environment happy-dom
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ContainerOutlet } from "@angee/ui/lib/container-outlet";
import { useSlot } from "@angee/ui/runtime";
import { ConsoleLayout } from "@angee/ui/layouts/ConsoleLayout";
import { isSurfaceSlotAdmitted, useSurfaceAdmission } from "@angee/ui/chrome/surface-policy";
import { RecordChrome } from "@angee/ui/views/index";

import { createApp } from "./create-app";
import { TEST_SCHEMAS } from "./testing";

afterEach(() => cleanup());

const record = {
  resource: "notes.Note",
  canonicalResource: "notes.Note",
  dataProviderName: undefined,
  recordId: "note-1",
  record: null,
  formReadOnly: false,
};

// A named slot of the page's own: the record form no longer reads slots, so
// surface admission is exercised on a plain one until surface itself goes.
const DESK_TOOLS_SLOT = "desk.tools";

function SlotPage() {
  const tools = useSlot(DESK_TOOLS_SLOT);
  const notices = useSlot("console.notice");
  const admission = useSurfaceAdmission();
  return <>
    <ContainerOutlet entries={tools} />
    <ContainerOutlet entries={notices} />
    <span data-testid="direct-share-admitted">{String(isSurfaceSlotAdmitted(admission, DESK_TOOLS_SLOT, "share"))}</span>
    <span data-testid="workflow-admitted">{String(isSurfaceSlotAdmitted(admission, DESK_TOOLS_SLOT, "workflow"))}</span>
  </>;
}

test("a confined app restricts a named slot, keeps an unnamed slot, and leaves public and sign-in routes unfiltered", async () => {
  history.replaceState(null, "", "/desk/one");
  const app = createApp({
    addons: [{
      id: "desk",
      routes: [
        { name: "desk.home", path: "/desk", component: SlotPage },
        { name: "desk.record", path: "/desk/$id", component: SlotPage },
        { name: "desk.public", path: "/open", layout: "public", component: SlotPage },
        { name: "desk.signin", path: "/signin", component: SlotPage },
      ],
      menus: [{ id: "desk", route: "desk.home" }],
      surface: [{ app: "desk", admit: { slots: { [DESK_TOOLS_SLOT]: ["share"] } } }],
      slots: [
        { slot: DESK_TOOLS_SLOT, id: "share", content: <span>Share action</span> },
        { slot: DESK_TOOLS_SLOT, id: "workflow", content: <span>Workflow action</span> },
        { slot: "console.notice", id: "notice", content: <span>Notice content</span> },
      ],
    }],
    layouts: { console: { requireAuth: false }, public: { requireAuth: false } },
    schemas: TEST_SCHEMAS,
    defaultSchema: "console",
    confineTo: "desk",
    home: "desk.home",
    loginPath: "/signin",
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = app.mount(host);
  try {
    await screen.findByText("Share action");
    expect(screen.queryByText("Workflow action")).toBeNull();
    expect(screen.getByText("Notice content")).toBeTruthy();
    expect(screen.getByTestId("direct-share-admitted").textContent).toBe("true");
    expect(screen.getByTestId("workflow-admitted").textContent).toBe("false");
    await act(async () => { await app.router.navigate({ to: "/open" }); });
    await screen.findByText("Workflow action");
    expect(screen.getByTestId("workflow-admitted").textContent).toBe("true");
    await act(async () => { await app.router.navigate({ to: "/signin" }); });
    expect(screen.getByText("Workflow action")).toBeTruthy();
  } finally { act(() => root.unmount()); host.remove(); }
});

test("an app narrows the record chrome with a conditional only on its own form#chrome children", async () => {
  history.replaceState(null, "", "/desk/one");
  const RecordPage = () => <RecordChrome value={record} />;
  const app = createApp({
    addons: [{
      id: "desk",
      routes: [
        { name: "desk.home", path: "/desk", component: RecordPage },
        { name: "desk.record", path: "/desk/$id", component: RecordPage },
      ],
      menus: [{ id: "desk", route: "desk.home" }],
      containers: {
        "form#chrome": [
          {
            "desk.share": { content: <span>Share action</span> },
            "desk.workflow": { content: <span>Workflow action</span> },
          },
          { only: ["desk.share"], when: { route: "desk.record" } },
        ],
      },
    }],
    layouts: { console: { requireAuth: false } },
    schemas: TEST_SCHEMAS,
    defaultSchema: "console",
    confineTo: "desk",
    home: "desk.home",
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = app.mount(host);
  try {
    await screen.findByText("Share action");
    expect(screen.queryByText("Workflow action")).toBeNull();
    await act(async () => { await app.router.navigate({ to: "/desk" }); });
    await screen.findByText("Workflow action");
    expect(screen.getByText("Share action")).toBeTruthy();
  } finally { act(() => root.unmount()); host.remove(); }
});

test("the console shell follows inherited breadcrumb and command search switches", async () => {
  history.replaceState(null, "", "/desk/item");
  const app = createApp({
    addons: [{
      id: "desk",
      routes: [
        { name: "desk.home", path: "/desk", component: () => <span>Home page</span> },
        { name: "desk.item", path: "/desk/item", component: () => <span>Item page</span> },
      ],
      menus: [{ id: "desk", route: "desk.home" }],
      surface: [{ app: "desk", route: "desk.item", shell: { breadcrumb: false, commandSearch: false } }],
    }],
    layouts: { console: { chrome: ConsoleLayout, requireAuth: false } },
    schemas: TEST_SCHEMAS,
    defaultSchema: "console",
    confineTo: "desk",
    home: "desk.home",
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = app.mount(host);
  try {
    await screen.findByText("Item page");
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open command palette" })).toBeNull();
    await act(async () => { await app.router.navigate({ to: "/desk" }); });
    await screen.findByText("Home page");
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeTruthy());
    expect(screen.getByRole("button", { name: "Open command palette" })).toBeTruthy();
  } finally { act(() => root.unmount()); host.remove(); }
});
