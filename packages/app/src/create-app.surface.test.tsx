// @vitest-environment happy-dom
import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ContainerOutlet } from "@angee/ui/lib/container-outlet";
import { useContainer } from "@angee/ui/runtime";
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

function ToolsPage() {
  const tools = useContainer("desk#tools");
  const notices = useContainer("shell#notices");
  return <>
    <ContainerOutlet entries={tools} />
    <ContainerOutlet entries={notices} />
  </>;
}

test("an app condition narrows the app's routes and their record children, never public and sign-in routes", async () => {
  history.replaceState(null, "", "/desk/one");
  const app = createApp({
    addons: [{
      id: "desk",
      routes: [
        { name: "desk.home", path: "/desk", component: ToolsPage },
        { name: "desk.record", path: "/desk/$id", parent: "desk.home", component: ToolsPage },
        { name: "desk.public", path: "/open", layout: "public", component: ToolsPage },
        { name: "desk.signin", path: "/signin", component: ToolsPage },
      ],
      menus: [{ id: "desk", route: "desk.home" }],
      containers: {
        // The addon declares its own container and narrows it inside its app.
        "desk#tools": [
          {
            "desk.share": { content: <span>Share action</span> },
            "desk.workflow": { content: <span>Workflow action</span> },
          },
          { only: ["desk.share"], when: { app: "desk" } },
        ],
        "shell#notices": { "desk.notice": { content: <span>Notice content</span> } },
      },
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
    await act(async () => { await app.router.navigate({ to: "/open" }); });
    await screen.findByText("Workflow action");
    await act(async () => { await app.router.navigate({ to: "/signin" }); });
    expect(screen.getByText("Workflow action")).toBeTruthy();
  } finally { act(() => root.unmount()); host.remove(); }
});

test("a route condition holds on the route and the routes below it", async () => {
  history.replaceState(null, "", "/desk/one");
  const app = createApp({
    addons: [{
      id: "desk",
      routes: [
        { name: "desk.home", path: "/desk", component: ToolsPage },
        { name: "desk.record", path: "/desk/$id", parent: "desk.home", component: ToolsPage },
        { name: "desk.other", path: "/elsewhere", component: ToolsPage },
      ],
      menus: [{ id: "desk", route: "desk.home" }, { id: "desk.other", route: "desk.other" }],
      containers: {
        "desk#tools": [
          { "desk.share": { content: <span>Share action</span> }, "desk.workflow": { content: <span>Workflow action</span> } },
          { "desk.workflow": { hide: true }, when: { route: "desk.home" } },
        ],
      },
    }],
    layouts: { console: { requireAuth: false } },
    schemas: TEST_SCHEMAS,
    defaultSchema: "console",
    home: "desk.home",
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = app.mount(host);
  try {
    await screen.findByText("Share action");
    expect(screen.queryByText("Workflow action")).toBeNull();
    await act(async () => { await app.router.navigate({ to: "/desk" }); });
    expect(screen.queryByText("Workflow action")).toBeNull();
    await act(async () => { await app.router.navigate({ to: "/elsewhere" }); });
    await screen.findByText("Workflow action");
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
