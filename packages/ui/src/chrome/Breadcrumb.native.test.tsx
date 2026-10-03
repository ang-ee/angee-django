// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { tanStackRouterProvider } from "@angee/refine";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, useRouterState } from "@tanstack/react-router";
import { afterEach, expect, test } from "vitest";

import { ConsoleLayout } from "../layouts/ConsoleLayout";
import { InAppLinkProvider } from "../lib/in-app-link";
import { AppRuntimeProvider, createRouteHref } from "../runtime";
import { createUiTestProviders } from "../testing";
import { TextLink } from "../ui/text-link";
import { RecordReference } from "../views/relation/RecordReference";
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
  useBreadcrumbLeafLabel(pathname === "/agents/demo" ? "Demo Agent" : pathname === "/models/one" ? "Model One" : pathname === "/models/two" ? "Model Two" : null);
  return <>
    <RecordReference model="agents.Agent" id="demo" label="Open agent" />
    <RecordReference model="models.Model" id="one" label="Follow model" />
    <TextLink href="/models/two?view=all">Follow another model</TextLink>
  </>;
}

function renderConsole(path = "/agents/demo") {
  const root = createRootRoute({ component: () => <InAppLinkProvider navigate={(href, options) => { void router.navigate({ href, ...options }); }}>
    <ui.Provider><AppRuntimeProvider runtime={{
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
  expect(router.state.location.state.trail).toEqual([
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
  expect(router.state.location.state.trail).toBeUndefined();
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
  expect(router.state.location.state.trail?.map((item) => item.label)).toEqual(["Agents", "Demo Agent"]);
  fireEvent.click(within(breadcrumb).getByRole("link", { name: "Demo Agent" }));
  await expectTrail("Agents/Demo Agent");
  expect(router.state.location.state.trail).toEqual([{ label: "Agents", href: "/agents" }]);
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
  expect(router.state.location.state.trail).toBeUndefined();
  await waitFor(() => expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull());
  fireEvent.click(screen.getByRole("link", { name: "Follow model" }));
  await expectTrail("Models/Model One");
});

test("following a content link to a menu destination also leaves the strip empty", async () => {
  const router = renderConsole();
  await expectTrail("Agents/Demo Agent");
  await router.navigate({ href: "/models", state: { trail: [{ label: "Agents", href: "/agents" }, { label: "Demo Agent", href: "/agents/demo" }] } });
  await waitFor(() => expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull());
});
