// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { tanStackRouterProvider } from "@angee/refine";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, useRouterState } from "@tanstack/react-router";
import { afterEach, expect, test } from "vitest";

import { ConsoleLayout } from "../layouts/ConsoleLayout";
import { InAppLinkProvider, routerNavigator } from "../lib/in-app-link";
import { AppRuntimeProvider, containersFromChildren, createRouteHref } from "../runtime";
import { createUiTestProviders } from "../testing";
import { TextLink } from "../ui/text-link";
import { RecordReference } from "../views/relation/RecordReference";
import { RoutedRecordController } from "../views/resource/resource-routing";
import { ResourceViewProvider, useResourceView } from "../views/resource/resource-view-context";
import { useBreadcrumbLeafLabel } from "./Breadcrumb";

const ui = createUiTestProviders({
  routerProvider: tanStackRouterProvider,
  resources: [testDataResource("agents.Agent"), testDataResource("models.Model")],
  refineResources: [
    { name: "menu:desk", list: "/desk", meta: { menuId: "desk", label: "Desk", appRoot: true } },
    { name: "agents", list: "/agents", show: "/agents/:id", meta: { menuId: "desk.agents", label: "Agents", parent: "menu:desk" } },
    { name: "models", list: "/models", show: "/models/:id", meta: { menuId: "desk.models", label: "Models", parent: "menu:desk" } },
  ],
});
afterEach(() => { cleanup(); ui.clearClients(); });

function Page() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const modelId = pathname.startsWith("/models/") ? pathname.split("/").at(-1) : undefined;
  useBreadcrumbLeafLabel(pathname === "/agents/demo" ? "Demo Agent" : modelId ? `Model ${modelId === "one" ? "One" : modelId === "two" ? "Two" : modelId}` : null);
  return <>
    <RecordReference model="agents.Agent" id="demo" label="Open agent" />
    <RecordReference model="models.Model" id="one" label="Follow model" />
    <TextLink href="/models/two?view=all">Follow another model</TextLink>
    <TextLink href="/models/one?view=other">Follow same model with another query</TextLink>
    {modelId && /^\d+$/.test(modelId) ? <TextLink href={`/models/${Number(modelId) + 1}`}>Next model</TextLink> : null}
    {modelId ? <RoutedRecordController resource="models.Model" newRecordId="new">
      {(controller) => <button onClick={() => controller.onRecordTabChange?.("details")}>Details tab</button>}
    </RoutedRecordController> : null}
    {modelId ? <ResourceViewProvider resource="models.Model"><ViewSearchControls /></ResourceViewProvider> : null}
  </>;
}

function ViewSearchControls() {
  const view = useResourceView();
  return <button onClick={() => view.setView("board")}>Board view</button>;
}

function renderConsole(path = "/agents/demo", drawer = false) {
  const root = createRootRoute({ component: () => <InAppLinkProvider navigate={navigate}>
    <ui.Provider><AppRuntimeProvider runtime={{
      ...(drawer ? { containers: containersFromChildren([{ address: "shell#drawers-right" }], {
        "shell#drawers-right": { related: { content: {
          title: "Related", render: () => <TextLink href="/models/one">Drawer model</TextLink>,
        } } },
      }) } : {}),
      routeHref: createRouteHref([
        { name: "agents", path: "/agents" }, { name: "agents.record", path: "/agents/$id" },
        { name: "models", path: "/models" }, { name: "models.record", path: "/models/$id" },
      ]),
      routesByResource: {
        "agents.Agent": { collection: "agents", record: { name: "agents.record", param: "id" } },
        "models.Model": { collection: "models", record: { name: "models.record", param: "id" } },
      },
    }}><ConsoleLayout><Page /></ConsoleLayout></AppRuntimeProvider></ui.Provider>
  </InAppLinkProvider> });
  const router = createRouter({
    routeTree: root.addChildren(["/desk", "/agents", "/agents/$id", "/models", "/models/$id"].map((path) => createRoute({ getParentRoute: () => root, path }))),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const navigate = routerNavigator(router);
  render(<RouterProvider router={router} />);
  return router;
}

async function expectTrail(text: string) {
  const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
  await waitFor(() => expect(breadcrumb.textContent).toBe(text));
  return breadcrumb;
}

test("a record reference carries the current form trail, and browser Back restores it", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  expect(router.state.location.state.breadcrumbTrail).toEqual([
    { label: "Agents", href: "/agents" }, { label: "Demo Agent", href: "/agents/demo" },
  ]);
  fireEvent.click(screen.getByRole("link", { name: "Follow another model" }));
  await expectTrail("Agents/Demo Agent/Model One/Model Two");
  expect(router.state.location.pathname).toBe("/models/two");
  expect(router.state.location.search).toEqual({ view: "all" });
  router.history.back();
  await expectTrail("Agents/Demo Agent/Model One");
  router.history.back();
  await expectTrail("Agents/Demo Agent");
  expect(router.state.location.state.breadcrumbTrail).toBeUndefined();
});

test("an earlier crumb truncates history and the joint is not repeated", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  fireEvent.click(screen.getByRole("link", { name: "Follow another model" }));
  const breadcrumb = await expectTrail("Agents/Demo Agent/Model One/Model Two");
  fireEvent.click(within(breadcrumb).getByRole("link", { name: "Model One" }));
  await expectTrail("Agents/Demo Agent/Model One");
  expect(router.state.location.state.breadcrumbTrail).toEqual([{ label: "Agents", href: "/agents" }, { label: "Demo Agent", href: "/agents/demo" }]);
  fireEvent.click(within(breadcrumb).getByRole("link", { name: "Demo Agent" }));
  await expectTrail("Agents/Demo Agent");
  expect(router.state.location.state.breadcrumbTrail).toEqual([{ label: "Agents", href: "/agents" }]);
});

test.each(["menu", "rail"])("%s navigation resets history and collection pages have no strip", async (chrome) => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  if (chrome === "menu") {
    const header = screen.getByRole("banner", { name: "Workspace top bar" });
    fireEvent.click(within(header).getByRole("link", { name: "Models" }));
  } else {
    fireEvent.click(within(screen.getByRole("complementary")).getByRole("link", { name: "Desk" }));
  }
  await waitFor(() => expect(router.state.location.pathname).toBe(chrome === "menu" ? "/models" : "/desk"));
  expect(router.state.location.state.breadcrumbTrail).toBeUndefined();
  await waitFor(() => expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull());
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Models/Model One");
});

test("following a content link to a menu destination also leaves the strip empty", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  await router.navigate({ href: "/models", state: { breadcrumbTrail: [{ label: "Agents", href: "/agents" }, { label: "Demo Agent", href: "/agents/demo" }] } });
  await waitFor(() => expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull());
});

test("switching a record tab preserves the nested breadcrumb history", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  const history = router.state.location.state.breadcrumbTrail;
  fireEvent.click(screen.getByRole("button", { name: "Details tab" }));
  await waitFor(() => expect(router.state.location.search).toEqual({ recordTab: "details" }));
  await expectTrail("Agents/Demo Agent/Model One");
  expect(router.state.location.state.breadcrumbTrail).toEqual(history);
});

test("same-location resource view updates preserve breadcrumb history", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  const history = router.state.location.state.breadcrumbTrail;
  fireEvent.click(screen.getByRole("button", { name: "Board view" }));
  await waitFor(() => expect(router.state.location.search.view).toBe("board"));
  await expectTrail("Agents/Demo Agent/Model One");
  expect(router.state.location.state.breadcrumbTrail).toEqual(history);
});

test("shell drawer content carries the current breadcrumb trail", async () => {
  renderConsole("/agents/demo", true);
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("button", { name: "Related" }));
  fireEvent.click(await screen.findByRole("link", { name: "Drawer model" }));
  await expectTrail("Agents/Demo Agent/Model One");
});

test("A to B to A cuts history at the revisited pathname", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  fireEvent.click(screen.getByRole("link", { name: "Open agent" }));
  await expectTrail("Agents/Demo Agent");
  expect(router.state.location.state.breadcrumbTrail).toEqual([{ label: "Agents", href: "/agents" }]);
});

test("following the same record with a different query replaces its current crumb", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Agents/Demo Agent/Model One");
  fireEvent.click(screen.getByRole("link", { name: "Follow same model with another query" }));
  await waitFor(() => expect(router.state.location.search).toEqual({ view: "other" }));
  await expectTrail("Agents/Demo Agent/Model One");
  fireEvent.click(screen.getByRole("link", { name: "Follow another model" }));
  const breadcrumb = await expectTrail("Agents/Demo Agent/Model One/Model Two");
  expect(within(breadcrumb).getByRole("link", { name: "Model One" }).getAttribute("href")).toBe("/models/one?view=other");
});

test("long navigation retains only the eight most recent crumbs", async () => {
  const router = renderConsole("/models/1");
  await expectTrail("Models/Model 1");
  for (let id = 2; id <= 12; id++) {
    fireEvent.click(screen.getByRole("link", { name: "Next model" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/models/${id}`));
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Breadcrumb" }).textContent?.endsWith(`Model ${id}`)).toBe(true));
  }
  await expectTrail(Array.from({ length: 8 }, (_, index) => `Model ${index + 5}`).join("/"));
  expect(router.state.location.state.breadcrumbTrail).toHaveLength(7);
});

test.each(["invalid", { label: "Ghost" }, [{ label: 7 }], [{ label: "Ghost", href: 7 }]])(
  "invalid history state falls back to the destination's native trail (%j)", async (breadcrumbTrail) => {
    const router = renderConsole();
    await expectTrail("Agents/Demo Agent");
    await router.navigate({ href: "/models/one", state: { breadcrumbTrail } });
    await expectTrail("Models/Model One");
  },
);
