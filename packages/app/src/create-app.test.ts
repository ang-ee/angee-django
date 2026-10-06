import { parse } from "graphql";
// @vitest-environment happy-dom

import { Fragment, createElement, useEffect, useState, type ReactNode } from "react";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/react";
import { createAngeeHasuraDataProvider } from "@angee/refine";
import { useBreadcrumb as useRefineBreadcrumb } from "@refinedev/core";
import { useAuthoredQuery } from "@angee/refine";
import {
  useAppRuntime,
  useChatterRoutes,
  useContainer,
  useT,
  useResourceRecordHrefLookup,
  useResourceRoute,
  useRouteHref,
  HOME_PATH_PREFERENCE_KEY,
} from "@angee/ui/runtime";
import { lazyRouteComponent, useParams } from "@tanstack/react-router";
import { ControlBand, ControlBandProvider } from "@angee/ui/layouts/ControlBand";
import { resourcePageRoutes } from "./define-base-addon";
import type { ShellSettings } from "./define-addon";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  createApp,
  parseFlatSearch,
  stringifyFlatSearch,
  type BaseAddon,
  type BaseAddonRoute,
  type CreateAppInput,
  type RefineLayoutChromeProps,
} from "./create-app";
import { MenuTree, type ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import { ChromePlaceProvider, useChromeMenuTree, useChromePlace } from "@angee/ui/chrome/refine-menu";
import {
  captureChrome,
  chromeSnapshot,
  testGraphQLFetch,
  TEST_SCHEMAS,
} from "./testing";
import {
  createResourceViewState,
  resourceViewSearchToState,
  resourceViewStateToSearch,
  mergeResourceViewSearch,
} from "@angee/ui/views/resource-view-model";
import { ResourceQuery, useModelMetadata, type DataResourceMetadata } from "@angee/metadata";
import type { ContainersDeclaration } from "@angee/ui/runtime";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { statusBadgeWidget } from "@angee/ui/widgets/statusBadge";

afterEach(() => cleanup());

test("search contributions validate shapes and model field capabilities at createApp", () => {
  const contract = ResourceQuery.forRows({ fields: { title: { scalar: "String" }, amount: { scalar: "Float" }, hidden: { scalar: "String" } } }).contract;
  const resource = testDataResource("notes.Note", { query: { ...contract, fields: { ...contract.fields,
    hidden: { ...contract.fields.hidden!, filter: { ...contract.fields.hidden!.filter!, operators: [] } },
  } } });
  const input = (content: unknown, address = "notes.Note#search") => ({ ...testAppInput([{
    id: "extension", containers: { [address]: { "extension.shortcut": { content } } } as ContainersDeclaration,
  }]), schemas: testSchemasWithConsoleResources([resource]) });
  expect(() => createApp(input({ kind: "text", field: "title" }))).not.toThrow();
  expect(() => createApp(input({ kind: "facet", field: "amount" }))).not.toThrow();
  expect(() => createApp(input({ kind: "text", field: "amount" }))).toThrow(/extension.shortcut.*amount.*iContains/);
  expect(() => createApp(input({ kind: "clause", field: "hidden" }))).toThrow(/extension.shortcut.*hidden.*not filterable/);
  expect(() => createApp(input({ kind: "facet", field: "unknown" }))).toThrow(/extension.shortcut.*unknown.*not filterable/);
  expect(() => createApp(input({ kind: "invalid" }, "resource#search"))).toThrow(/extension.shortcut.*unknown kind/);
  expect(() => createApp(input({ kind: "toggle", id: 7 }, "resource#search"))).toThrow(/extension.shortcut.*string id/);
  // Kind-level fields and toggle targets require the rendering list's catalog.
  expect(() => createApp(input({ kind: "clause", field: "unknown" }, "resource#search"))).not.toThrow();
  expect(() => createApp(input({ kind: "toggle", id: "list-owned" }))).not.toThrow();
});

describe("route-owned chrome", () => {
  test("a parameterized destination beats the dashboard anchor in chrome, app scope and native breadcrumbs", async () => {
    let captured: { item?: string; app?: string; activeApp?: string | null; breadcrumbs: ReactNode[] } | undefined;
    function CapturePlace(): ReactNode {
      const { match } = useChromePlace();
      const { activeApp } = useAppRuntime();
      const breadcrumbs = useRefineBreadcrumb().breadcrumbs;
      useEffect(() => {
        captured = { item: match?.item.id, app: match?.app?.id, activeApp, breadcrumbs: breadcrumbs.map((item) => item.label) };
      }, [match, activeApp, breadcrumbs]);
      return null;
    }
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/dashboards/addon/accounts-payable");
    const app = createApp(testAppInput([
      { id: "dashboards", routes: [
        { name: "dashboards.index", path: "/dashboards", component: EmptyPage },
        { name: "dashboards.addon", path: "/dashboards/addon/$key", component: EmptyPage, menu: "dashboards" },
      ], menus: [{ id: "dashboards", label: "Dashboards", route: "dashboards.index" }] },
      { id: "accounting", menus: [{ id: "accounting", label: "Accounting", children: [
        { id: "accounting.vendors", label: "Vendors", children: [
          { id: "accounting.payable", label: "Accounts Payable", route: "dashboards.addon", params: { key: "accounts-payable" } },
        ] },
      ] }] },
    ], { console: { requireAuth: false, chrome: () => createElement(ChromePlaceProvider, { children: createElement(CapturePlace) }) } }));
    const root = app.mount(host);
    try {
      await waitFor(() => expect(captured?.item).toBe("accounting.payable"));
      expect(captured).toMatchObject({ app: "accounting", activeApp: "accounting" });
      expect(captured?.breadcrumbs).toEqual(["Accounting", "Vendors", "Accounts Payable"]);
      expect(app.explain.menus.diagnostics).toEqual([]);
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test.each([
    { owner: "workflows", label: "Workflows", menu: "workflows.runs", itemLabel: "Runs",
      route: "workflows.runs", path: "/workflows/runs", model: "workflows.WorkflowRun",
      foreign: "integrate-odoo", foreignLabel: "Odoo", groupLabel: "Diagnostics" },
    { owner: "arp-base", label: "Companies", menu: "arp-base.companies", itemLabel: "Companies",
      route: "arp.companies", path: "/companies", model: "arp.Company",
      foreign: "accounting", foreignLabel: "Accounting", groupLabel: "Configuration" },
  ])("$label keeps list and record chrome when a deeper foreign item targets its route", async (fixture) => {
    const addons: BaseAddon[] = [
      { id: fixture.owner, routes: resourcePageRoutes(fixture.route, fixture.path, EmptyPage, fixture.model, { menu: fixture.menu }),
        menus: [{ id: fixture.owner, label: fixture.label, children: [
          { id: fixture.menu, label: fixture.itemLabel, route: fixture.route },
        ] }] },
      { id: fixture.foreign, menus: [{ id: fixture.foreign, label: fixture.foreignLabel, children: [
        { id: `${fixture.foreign}.group`, label: fixture.groupLabel, children: [
          { id: `${fixture.foreign}.link`, label: "Foreign link", route: fixture.route },
        ] },
      ] }] },
    ].reverse();
    for (const path of [fixture.path, `${fixture.path}/record-1`]) {
      let captured: { item?: string; app?: string; activeApp?: string | null; breadcrumbs: ReactNode[] } | undefined;
      function CapturePlace(): ReactNode {
        const { match } = useChromePlace();
        const { activeApp } = useAppRuntime();
        const breadcrumbs = useRefineBreadcrumb().breadcrumbs;
        useEffect(() => {
          captured = { item: match?.item.id, app: match?.app?.id, activeApp, breadcrumbs: breadcrumbs.map((item) => item.label) };
        }, [match, activeApp, breadcrumbs]);
        return null;
      }
      const host = document.createElement("div");
      document.body.append(host);
      history.replaceState(null, "", path);
      const app = createApp({
        ...testAppInput(addons, { console: { requireAuth: false,
          chrome: () => createElement(ChromePlaceProvider, { children: createElement(CapturePlace) }) } }),
        schemas: testSchemasWithConsoleResources([testDataResource(fixture.model)]),
      });
      const root = app.mount(host);
      try {
        await waitFor(() => expect(captured).toBeDefined());
        expect(captured).toMatchObject({ item: fixture.menu, app: fixture.owner, activeApp: fixture.owner });
        expect(captured?.breadcrumbs).toContain(path === fixture.path ? fixture.label : fixture.itemLabel);
        expect(captured?.breadcrumbs).not.toContain(fixture.foreignLabel);
        expect(captured?.breadcrumbs).not.toContain(fixture.groupLabel);
        expect(app.explain.menus.diagnostics).toEqual([]);
      } finally {
        root.unmount();
        host.remove();
      }
    }
  });
});

describe("createApp selection", () => {
  const addons: readonly BaseAddon[] = [{
    id: "requests",
    routes: [
      { name: "requests.all", path: "/requests", component: EmptyPage },
      { name: "requests.record", path: "/requests/$id", parent: "requests.all", component: EmptyPage },
      { name: "files.all", path: "/files", component: EmptyPage },
      { name: "files.record", path: "/files/$id", parent: "files.all", component: EmptyPage },
      { name: "account", path: "/account", component: EmptyPage },
      { name: "public.page", path: "/public-page", layout: "public", component: EmptyPage },
    ],
    menus: [
      { id: "requests", home: "requests.all", children: [{ id: "requests.all", route: "requests.all" }] },
      { id: "files", route: "files.all" },
    ],
  }];
  const layouts = { console: { requireAuth: false }, public: { requireAuth: false } } as const;
  const deployment = (shell: ShellSettings): BaseAddon => ({ id: "deployment", dependsOn: ["requests"], shell });

  test("?app= wins over the hostname, which wins over nothing; an unknown ?app= falls back and says why", () => {
    const shell = deployment({ apps: { both: { rail: ["files", "requests"] } }, hosts: { "files.localhost": "files" } });
    const input = { ...testAppInput([...addons, shell], layouts) };
    const select = (location: CreateAppInput["location"]) => createApp({ ...input, location }).explain.selection;
    expect(select({ search: "?app=both", hostname: "files.localhost" })).toMatchObject({
      app: "both", rail: ["files", "requests"], sources: { app: "?app=both", rail: "ANGEE_UI.shell.apps.both" },
    });
    expect(select({ search: "", hostname: "files.localhost" })).toMatchObject({
      app: "files", rail: ["files"], sources: { app: 'ANGEE_UI.shell.hosts["files.localhost"]' },
    });
    expect(select({ search: "?app=nowhere", hostname: "files.localhost" })).toMatchObject({
      app: "files", diagnostics: ["?app=nowhere names no menu root and no ANGEE_UI.shell.apps entry, so it selects nothing."],
    });
    expect(select({ search: "?app=nowhere", hostname: "elsewhere.localhost" })).toMatchObject({ app: null, rail: null, brand: null });
    expect(select({ search: "" })).toMatchObject({ app: null, rail: null, diagnostics: [] });
    // Without a location the page's own URL selects.
    const { location: _location, ...unlocated } = input;
    history.replaceState(null, "", "/files?app=requests");
    try {
      expect(createApp(unlocated).explain.selection.app).toBe("requests");
    } finally {
      history.replaceState(null, "", "/");
    }
  });

  test("checks every root, app and host at boot, selected or not", () => {
    const input = (menus: BaseAddon["menus"], shell?: ShellSettings): CreateAppInput => testAppInput(
      [{ ...addons[0]!, menus }, ...(shell ? [deployment(shell)] : [])], layouts);
    const files = { id: "files", route: "files.all" };
    const requests = (home: string) => ({ id: "requests", home, children: [{ id: "requests.all", route: "requests.all" }] });
    expect(() => createApp(input([requests("files.all"), files]))).toThrow(
      'Menu root "requests" home "files.all" lies outside its rail (requests), in menu root "files".',
    );
    expect(() => createApp(input([requests("account"), files]))).toThrow(
      'Menu root "requests" home "account" lies outside its rail (requests), in no single menu root; anchor the route with route.menu.',
    );
    const valid = [requests("requests.all"), files];
    expect(() => createApp(input(valid, { apps: { focus: { rail: ["requests"], home: "files.all" } } }))).toThrow(
      'ANGEE_UI.shell.apps.focus home "files.all" lies outside its rail (requests), in menu root "files".',
    );
    expect(() => createApp(input(valid, { apps: { focus: { rail: ["requests", "requests.all"] } } }))).toThrow(
      'ANGEE_UI.shell.apps.focus rail names "requests.all", which is not a top-level menu root.',
    );
    expect(() => createApp(input(valid, { apps: { files: { rail: ["requests"] } } }))).toThrow(
      'ANGEE_UI.shell.apps names "files", a menu root id; name the app apart from the roots.',
    );
    expect(() => createApp(input(valid, { hosts: { "requests.localhost": "nothing" } }))).toThrow(
      'ANGEE_UI.shell.hosts["requests.localhost"] selects "nothing", which is neither a menu root id nor an ANGEE_UI.shell.apps name.',
    );
    expect(() => createApp(input(valid, { hosts: { "Requests.localhost": "requests" } }))).toThrow(
      'ANGEE_UI.shell.hosts["Requests.localhost"] must be a lower-case hostname.',
    );
    expect(() => createApp(input(valid, { apps: { focus: { rail: [] } } }))).toThrow(
      "ANGEE_UI.shell.apps.focus.rail must be a non-empty list of menu root ids.",
    );
    expect(() => createApp(input(valid, { rail: ["requests"] } as unknown as ShellSettings))).toThrow(
      'ANGEE_UI.shell has unknown key "rail".',
    );
    expect(() => createApp(input(valid, { apps: { focus: { rail: ["requests"], home: "requests.all" } }, hosts: { "requests.localhost": "focus" } })))
      .not.toThrow();
  });

  test("a named app takes its own brand, theme and home, else its first rail root's", () => {
    const icons = { mark: () => null };
    const withMark = { ...addons[0]!, icons };
    const select = (shell: ShellSettings, search: string) => createApp({
      ...testAppInput([withMark, deployment(shell)], layouts), location: { search },
    }).explain.selection;
    expect(select({ apps: { focus: { rail: ["requests", "files"] } } }, "?app=focus")).toEqual({
      app: "focus",
      rail: ["requests", "files"],
      brand: { name: "Requests", mark: "requests" },
      home: "requests.all",
      sources: {
        app: "?app=focus",
        rail: "ANGEE_UI.shell.apps.focus",
        brand: 'menu root "requests" label and icon',
        home: 'menu root "requests"',
      },
      diagnostics: [],
    });
    expect(select({ apps: { focus: { rail: ["files", "requests"], brand: { name: "Focus", mark: "mark" }, home: "requests.all" } } }, "?app=focus"))
      .toMatchObject({
        brand: { name: "Focus", mark: "mark" }, home: "requests.all",
        sources: { brand: "ANGEE_UI.shell.apps.focus", home: "ANGEE_UI.shell.apps.focus" },
      });
    // A bare root selects itself; the deployment's brand is only for nothing selected.
    expect(select({ brand: { name: "Desk", mark: "mark" } }, "?app=files")).toMatchObject({
      app: "files", rail: ["files"], brand: { name: "Files", mark: "files" },
    });
    expect(select({ brand: { name: "Desk", mark: "mark" } }, "")).toMatchObject({
      app: null, rail: null, brand: { name: "Desk", mark: "mark" }, sources: { brand: "ANGEE_UI.shell.brand" },
    });
  });

  test("accepts a root and first child sharing the resource route", () => {
    const shared: BaseAddon = {
      id: "requests",
      routes: [{ name: "requests.all", path: "/requests", resource: "requests.Request", component: EmptyPage }],
      menus: [{
        id: "requests",
        route: "requests.all",
        children: [{ id: "requests.all", route: "requests.all" }],
      }],
    };
    const input = {
      ...testAppInput([shared], { console: { requireAuth: false } }),
      schemas: testSchemasWithConsoleResources([testDataResource("requests.Request")]),
    };
    expect(() => createApp(input)).not.toThrow();
    expect(() => createApp({ ...input, location: { search: "?app=requests" } })).not.toThrow();
  });

  test("a dependent's own page of a model boots beside its base's, selected or not; unrelated claimants fail", () => {
    const tasks: BaseAddon = {
      id: "tasks",
      routes: resourcePageRoutes("tasks.all", "/tasks", EmptyPage, "tasks.Task"),
      menus: [{ id: "tasks", route: "tasks.all" }],
    };
    const desk: BaseAddon = {
      id: "desk",
      dependsOn: ["tasks"],
      routes: resourcePageRoutes("desk.tasks", "/desk/tasks", EmptyPage, "tasks.Task"),
      menus: [{ id: "desk", children: [{ id: "desk.tasks", route: "desk.tasks" }] }],
    };
    const input = (addons: readonly BaseAddon[]): CreateAppInput => ({
      ...testAppInput(addons, { console: { requireAuth: false } }),
      schemas: testSchemasWithConsoleResources([testDataResource("tasks.Task")]),
    });
    for (const search of ["", "?app=desk", "?app=tasks"]) {
      expect(() => createApp({ ...input([tasks, desk]), location: { search } })).not.toThrow();
    }
    expect(() => createApp(input([tasks, { ...desk, dependsOn: [] }])))
      .toThrow(/claim resource "tasks.Task" from addons that do not depend on one another/);
  });

  test("a route two roots reference has no owner unless route.menu anchors it, selected or not", () => {
    const shared: BaseAddon = {
      id: "requests",
      routes: [{ name: "requests.all", path: "/requests", resource: "requests.Request", component: EmptyPage }],
      menus: [
        { id: "requests", route: "requests.all", home: "requests.all" },
        { id: "files", route: "requests.all" },
      ],
    };
    const input = {
      ...testAppInput([shared], { console: { requireAuth: false } }),
      schemas: testSchemasWithConsoleResources([testDataResource("requests.Request")]),
    };
    expect(() => createApp(input)).toThrow(/home "requests.all" lies outside its rail \(requests\), in no single menu root/);
    const explicit = {
      ...shared,
      routes: shared.routes?.map((route) => ({ ...route, menu: "requests" })),
    };
    expect(() => createApp({ ...input, addons: [explicit] })).not.toThrow();
    expect(() => createApp({ ...input, addons: [explicit], location: { search: "?app=files" } })).not.toThrow();
  });

  test("projects only the selected rail into both navigation sources", async () => {
    const captured = await captureChrome({
      addons,
      path: "/requests/item-1",
      app: "requests",
    });
    try {
      const tree = MenuTree.from(captured.props().menus);
      expect(tree.railMenuItems().map((item) => item.id)).toEqual(["requests"]);
      expect(tree.navigableItems().map(({ item }) => item.id)).toEqual(["requests.all"]);
    } finally {
      captured.cleanup();
    }
  });

  test("a page only a removed menu item reached still redirects home, with an app selected or not", async () => {
    const trimmed: readonly BaseAddon[] = [...addons, { id: "trim", dependsOn: ["requests"], menus: { files: { remove: true } } }];
    for (const search of ["", "?app=requests"]) {
      history.replaceState(null, "", "/files/item-1");
      const app = createApp({ ...testAppInput(trimmed, layouts), location: { search } });
      expect(app.explain.menus.unavailable).toEqual({
        "files.all": 'menu item "files" was removed',
        "files.record": 'its parent route "files.all" is unavailable',
      });
      const host = document.createElement("div");
      document.body.append(host);
      const root = app.mount(host);
      try {
        await waitFor(() => expect(window.location.pathname).toBe("/requests"));
        await app.router.navigate({ to: "/account" });
        expect(window.location.pathname).toBe("/account");
      } finally {
        root.unmount();
        host.remove();
      }
    }
  });

  test("lands on the selected app's home and leaves every route outside its rail reachable", async () => {
    history.replaceState(null, "", "/");
    const app = createApp({ ...testAppInput(addons, layouts), location: { search: "?app=requests" } });
    expect(app.explain.home).toBe("/requests");
    const host = document.createElement("div");
    document.body.append(host);
    const root = app.mount(host);
    try {
      const length = history.length;
      await waitFor(() => expect(window.location.pathname).toBe("/requests"));
      expect(history.length).toBe(length);
      await app.router.navigate({ to: "/public-page" });
      expect(window.location.pathname).toBe("/public-page");
      await app.router.navigate({ to: "/requests/item-1" });
      expect(window.location.pathname).toBe("/requests/item-1");
      await app.router.navigate({ to: "/account" });
      expect(window.location.pathname).toBe("/account");
      await app.router.navigate({ to: "/files/item-1" });
      expect(window.location.pathname).toBe("/files/item-1");
      await app.router.navigate({ to: "/files" });
      expect(window.location.pathname).toBe("/files");
    } finally {
      root.unmount();
      host.remove();
    }
  });
});

describe("createApp presence", () => {
  const ALL_REFS = ["iam.User#create", "iam.User#delete", "iam.User#read", "iam.User#read__last_login"];
  function DirectoryPage(): ReactNode {
    const tools = useContainer("people#tools");
    return createElement("p", null, `people.directory page [${tools.map((child) => child.id).join(",")}]`);
  }
  const page = (name: string, path: string): BaseAddonRoute => ({
    name, path, component: () => createElement("p", null, `${name} page`),
  });
  const people: BaseAddon = {
    id: "people",
    routes: [
      { name: "people.directory", path: "/people", component: DirectoryPage },
      page("people.manage", "/people/manage"),
      page("people.audit", "/people/audit"),
      page("people.log", "/people/log"),
    ],
    menus: {
      people: { label: "People" },
      "people.directory": { parent: "people", route: "people.directory" },
      "people.manage": { parent: "people", route: "people.manage", requires: "iam.User#create" },
      "people.reviews": { parent: "people", label: "Reviews", requires: "iam.User#read__last_login" },
      "people.audit": { parent: "people.reviews", route: "people.audit" },
      "people.log": { label: "Log", route: "people.log", requires: "iam.User#delete" },
    },
    containers: {
      "people#tools": {
        "people.export": { content: null, requires: "iam.User#read" },
        "people.print": { content: null },
      },
    },
  };

  /** Mount at `path` for a session whose identity holds `permitted`, recording the refs each identity read asks about. */
  function mountFor(path: string, permitted: readonly string[]) {
    let tree: MenuTree | undefined;
    const asked: unknown[] = [];
    function CaptureChrome({ children }: RefineLayoutChromeProps): ReactNode {
      const menu = useChromeMenuTree();
      useEffect(() => { tree = menu; }, [menu]);
      return children;
    }
    const fetch: typeof globalThis.fetch = async (input, init) => {
      const body = typeof init?.body === "string" ? init.body : input instanceof Request ? await input.clone().text() : "";
      const refs = body ? (JSON.parse(body) as { variables?: { refs?: unknown } }).variables?.refs : undefined;
      if (refs !== undefined) asked.push(refs);
      return Response.json({ data: {
        current_user: {
          id: "user-1", username: "user", firstName: "", lastName: "", email: "", isStaff: false, isActive: true,
          roleRefs: [], permitted, preferences: {},
        },
        real_user: null,
        viewable_people: [],
      } });
    };
    history.replaceState(null, "", path);
    const app = createApp({
      ...testAppInput([people], { console: { chrome: CaptureChrome, requireAuth: true } }),
      schemas: { public: { ...TEST_SCHEMAS.public, fetch }, console: { ...TEST_SCHEMAS.console, fetch } },
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = app.mount(host);
    return {
      app,
      host,
      asked,
      palette: () => tree?.navigableItems().map(({ item }) => item.id).sort(),
      rail: () => tree?.railMenuItems().map((item) => item.id),
      has: (id: string) => tree?.byId.has(id),
      cleanup: () => { root.unmount(); host.remove(); },
    };
  }

  test("an entry the identity lacks a ref for is absent from the rail and palette with its subtree; its page stays reachable", async () => {
    const mounted = mountFor("/people/manage", []);
    try {
      await waitFor(() => expect(mounted.host.textContent).toContain("people.manage page"));
      expect(mounted.app.router.state.location.pathname).toBe("/people/manage");
      await waitFor(() => expect(mounted.palette()).toEqual(["people.directory"]));
      expect(mounted.rail()).toEqual(["people"]);
      for (const id of ["people.manage", "people.reviews", "people.audit", "people.log"]) expect(mounted.has(id)).toBe(false);
      // A page whose entry and its parent are absent still opens.
      await mounted.app.router.navigate({ to: "/people/audit" });
      await waitFor(() => expect(mounted.host.textContent).toContain("people.audit page"));
      // Container children follow the same refs.
      await mounted.app.router.navigate({ to: "/people" });
      await waitFor(() => expect(mounted.host.textContent).toContain("people.directory page [people.print]"));
      // The console read, which yields the identity, asks about every composed ref; the public read about none.
      expect(mounted.asked).toContainEqual(ALL_REFS);
      expect(mounted.asked).toContainEqual([]);
    } finally {
      mounted.cleanup();
    }
  });

  test("holding every ref, the declared navigation and children are present", async () => {
    const mounted = mountFor("/people", ALL_REFS);
    try {
      await waitFor(() => expect(mounted.host.textContent).toContain("people.directory page [people.export,people.print]"));
      await waitFor(() => expect(mounted.palette()).toEqual(["people.audit", "people.directory", "people.log", "people.manage"]));
      expect(mounted.rail()).toEqual(["people", "people.log"]);
      expect(mounted.app.explain.requires).toEqual({
        menus: { "people.manage": "iam.User#create", "people.reviews": "iam.User#read__last_login", "people.log": "iam.User#delete" },
        containers: { "people#tools/people.export": "iam.User#read" },
      });
    } finally {
      mounted.cleanup();
    }
  });

  test("a requires not shaped <app_label.ModelName>#<permission> fails at boot", () => {
    const routes = [page("people.directory", "/people")];
    expect(() => createApp(testAppInput([{ id: "people", routes, menus: {
      people: { label: "People", route: "people.directory", requires: "manage people" },
    } }]))).toThrow('Menu item "people" requires "manage people", which is not "<app_label.ModelName>#<permission>".');
    expect(() => createApp(testAppInput([{ id: "people", routes, menus: { people: { route: "people.directory" } },
      containers: { "people#tools": { "people.export": { content: null, requires: "iam.User" } } } }])))
      .toThrow(/Child "people.export" of "people#tools" requires "iam.User"/);
  });
});

describe("createApp developer mode", () => {
  test("?debug=1 on / turns it on through the home redirect; pages read the composition and route name", async () => {
    window.sessionStorage.clear();
    history.replaceState(null, "", "/?debug=1");
    const seen: { route?: string | null; home?: string } = {};
    function Probe(): ReactNode {
      const runtime = useAppRuntime();
      seen.route = runtime.activeRouteName;
      seen.home = runtime.composition?.home;
      return null;
    }
    const app = createApp(
      testAppInput([{ id: "desk", routes: [{ name: "desk.home", path: "/desk", component: Probe }], menus: [{ id: "desk", route: "desk.home" }] }]),
    );
    const host = document.createElement("div");
    document.body.append(host);
    const root = app.mount(host);
    try {
      await waitFor(() => expect(window.location.pathname).toBe("/desk"));
      expect(window.sessionStorage.getItem("angee:developer-mode")).toBe("1");
      await waitFor(() => expect(seen).toEqual({ route: "desk.home", home: "/desk" }));
      expect(app.explain.home).toBe("/desk");
    } finally {
      root.unmount();
      host.remove();
      window.sessionStorage.clear();
    }
  });
});

type AuthoredQueryDocument = Parameters<typeof useAuthoredQuery>[0];

function typedDocument(source: string): AuthoredQueryDocument {
  return parse(source) as AuthoredQueryDocument;
}

describe("createApp search codec", () => {
  test("round-trips the login next parameter as a flat string", () => {
    const next = "/notes?page=2&view=board&group=status:year";

    const query = stringifyFlatSearch({ next });

    expect(query).toBe(
      "?next=%2Fnotes%3Fpage%3D2%26view%3Dboard%26group%3Dstatus%3Ayear",
    );
    expect(query).not.toContain("%22");
    expect(parseFlatSearch(query).next).toBe(next);
  });

  test("keeps primitive resource-view search values unquoted", () => {
    const query = stringifyFlatSearch({
      page: 2,
      view: "board",
      group: "status:year",
      sort: "title:asc",
      empty: "",
      nil: null,
    });

    const parsed = parseFlatSearch(query);
    expect(parsed).toEqual({
      page: "2",
      view: "board",
      group: "status:year",
      sort: "title:asc",
      empty: "",
    });
    expect(query).not.toContain("%22board%22");
  });

  test("preserves foreign search keys when resource-view state changes", () => {
    const current = parseFlatSearch(
      "?tab=archive&page=2&view=board&group=status:year",
    );
    const currentState = resourceViewSearchToState(current);
    const nextState = {
      ...currentState,
      pagination: { ...currentState.pagination, pageIndex: 0 },
      sorting: [{ id: "title", desc: false }],
    };

    const query = stringifyFlatSearch(
      mergeResourceViewSearch(current, resourceViewStateToSearch(nextState)),
    );
    const parsed = parseFlatSearch(query);

    expect(parsed.tab).toBe("archive");
    expect(parsed.sort).toBe("title:asc");
    expect(parsed.group).toBe("status:year");
    expect(parsed.view).toBe("board");
    expect(parsed.page).toBeUndefined();
    expect(query).toContain("tab=archive");
    expect(query).not.toContain("%22");
  });

  test("round-trips explicit clears of seeded resource state and foreign empty search", () => {
    const initial = {
      page: 3,
      sort: { field: "title", dir: "asc" as const },
      filter: { title: { iContains: "alpha" } },
      groupStack: [{ field: "status" }, { field: "owner" }],
    };
    const cleared = createResourceViewState({ page: 1 });
    const query = stringifyFlatSearch(mergeResourceViewSearch(
      { keep: "external", empty: "" },
      resourceViewStateToSearch(cleared, initial),
    ));
    const parsed = parseFlatSearch(query);

    expect(parsed).toEqual({ keep: "external", empty: "", page: "1", sort: "", filter: "", group: "", then: "" });
    expect(resourceViewSearchToState(parsed, initial)).toMatchObject({
      pagination: { pageIndex: 0 }, sorting: [], filter: {}, groupStack: [],
    });
  });
});

describe("createApp schema binding", () => {
  test("pins public layout routes and lets console routes inherit the default schema", async () => {
    const seen: Record<string, string> = {};
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/public-page");
    const publicProbe = typedDocument("query PublicProbe { schemaProbe }");
    const consoleProbe = typedDocument("query ConsoleProbe { schemaProbe }");

    function PublicPage(): ReactNode {
      useAuthoredQuery(publicProbe);
      return createElement("span", null, "Public probe");
    }

    function ConsolePage(): ReactNode {
      useAuthoredQuery(consoleProbe);
      return createElement("span", null, "Console probe");
    }

    const app = createApp({
      addons: [
        {
          id: "schema-test",
          routes: [
            {
              name: "public.page",
              path: "/public-page",
              layout: "public",
              component: PublicPage,
            },
            {
              name: "console.page",
              path: "/console-page",
              layout: "console",
              component: ConsolePage,
            },
          ],
        },
      ],
      defaultSchema: "console",
      subscriptionSchema: "console",
      layouts: {
        public: {
          chrome: TestChrome,
          requireAuth: false,
          schema: "public",
        },
        console: {
          chrome: TestChrome,
          requireAuth: false,
        },
      },
      schemas: {
        public: {
          url: "https://example.test/graphql/public/",
          fetch: probeFetch("public", seen),
        },
        console: {
          url: "https://example.test/graphql/console/",
          fetch: probeFetch("console", seen),
        },
      },
    });

    const root = app.mount(host);
    await waitFor(() => {
      expect(host.textContent).toContain("Public probe");
    });
    await waitFor(() =>
      expect(seen.public).toBe("https://example.test/graphql/public/"),
    );

    history.pushState(null, "", "/console-page");
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => {
      expect(host.textContent).toContain("Console probe");
    });
    await waitFor(() =>
      expect(seen.console).toBe("https://example.test/graphql/console/"),
    );
    root.unmount();
  });
});

describe("createApp addon data providers", () => {
  test("registers an addon-contributed provider for reads under its name", async () => {
    const seen: Record<string, string> = {};
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/operator-page");
    const operatorProbe = typedDocument("query OperatorProbe { schemaProbe }");

    function OperatorPage(): ReactNode {
      useAuthoredQuery(operatorProbe, undefined, {
        dataProviderName: "operator",
      });
      return createElement("span", null, "Operator probe");
    }

    const app = createApp({
      addons: [
        {
          id: "operator",
          routes: [
            {
              name: "operator.page",
              path: "/operator-page",
              layout: "console",
              component: OperatorPage,
            },
          ],
          dataProviders: {
            operator: createAngeeHasuraDataProvider({
              url: "https://example.test/operator/graphql/",
              fetch: probeFetch("operator", seen),
            }),
          },
        },
      ],
      defaultSchema: "console",
      subscriptionSchema: "console",
      layouts: {
        console: { chrome: TestChrome, requireAuth: false },
      },
      schemas: TEST_SCHEMAS,
    });

    const root = app.mount(host);
    try {
      await waitFor(() => {
        expect(host.textContent).toContain("Operator probe");
      });
      await waitFor(() =>
        expect(seen.operator).toBe("https://example.test/operator/graphql/"),
      );
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("rejects an addon provider that shadows a configured schema name", () => {
    expect(() =>
      createApp(
        testAppInput([
          {
            id: "shadow",
            routes: [
              {
                name: "shadow.home",
                path: "/shadow",
                layout: "console",
                component: EmptyPage,
              },
            ],
            dataProviders: {
              console: createAngeeHasuraDataProvider({
                url: "https://example.test/operator/graphql/",
                fetch: testGraphQLFetch,
              }),
            },
          },
        ]),
      ),
    ).toThrow(/collides with a schema-named provider/);
  });
});

describe("createApp auth routing", () => {
  test("redirects protected layouts from beforeLoad before rendering the page", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/private?tab=activity");
    let privateRendered = false;

    function PrivatePage(): ReactNode {
      privateRendered = true;
      return createElement("span", null, "Private page");
    }

    function LoginPage(): ReactNode {
      return createElement("span", null, "Login page");
    }

    const app = createApp({
      addons: [
        {
          id: "auth-routing",
          routes: [
            {
              name: "auth.login",
              path: "/sign-in",
              layout: "public",
              component: LoginPage,
            },
            {
              name: "auth.private",
              path: "/private",
              layout: "console",
              component: PrivatePage,
            },
          ],
        },
      ],
      defaultSchema: "console",
      subscriptionSchema: "console",
      loginPath: "/sign-in",
      layouts: {
        public: {
          chrome: TestChrome,
          requireAuth: false,
          schema: "public",
        },
        console: {
          chrome: TestChrome,
          requireAuth: true,
        },
      },
      schemas: TEST_SCHEMAS,
    });

    const root = app.mount(host);
    try {
      await waitFor(() => {
        expect(window.location.pathname).toBe("/sign-in");
      });

      expect(new URLSearchParams(window.location.search).get("next")).toBe(
        "/private?tab=activity",
      );
      expect(host.textContent).toContain("Login page");
      expect(privateRendered).toBe(false);
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("a signed-out visit to / signs in to come back to /, not to the home it would land on", async () => {
    history.replaceState(null, "", "/");
    const app = createApp({
      ...testAppInput([{ id: "desk", routes: [
        { name: "auth.login", path: "/sign-in", layout: "public", component: EmptyPage },
        { name: "desk.home", path: "/desk", component: EmptyPage },
      ] }], {
        public: { chrome: TestChrome, requireAuth: false, schema: "public" },
        console: { chrome: TestChrome, requireAuth: true },
      }),
      loginPath: "/sign-in",
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = app.mount(host);
    try {
      await waitFor(() => expect(window.location.pathname).toBe("/sign-in"));
      expect(new URLSearchParams(window.location.search).get("next")).toBe("/");
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test.each([
    { case: "no preferences", preferences: {}, lands: "/notes" },
    { case: "their own rail order", preferences: { "chrome.rail": { order: ["ops"] } }, lands: "/ops" },
    { case: "their rail default", preferences: { "chrome.rail": { order: ["ops"], defaultItemId: "notes" } }, lands: "/notes" },
    { case: "their saved home page", preferences: { homePath: "/platform/graph" }, lands: "/platform/graph" },
  ])("a signed-in visit to / waits for sign-in and lands by $case", async ({ preferences, lands }) => {
    const user = { id: "ada", username: "ada", firstName: "Ada", lastName: "", email: "", isStaff: false, isActive: true, preferences, roleRefs: [] };
    const fetch: typeof globalThis.fetch = async () => Response.json({ data: { __typename: "Query", current_user: user } });
    history.replaceState(null, "", "/");
    // Settings is composed first; it is still not where a person lands.
    const app = createApp({
      ...testAppInput([
        { id: "platform", routes: [{ name: "platform.graph", path: "/platform/graph", component: EmptyPage }],
          menus: [{ id: "platform", route: "platform.graph", group: "platform" }] },
        { id: "notes", routes: [{ name: "notes.home", path: "/notes", component: EmptyPage }], menus: [{ id: "notes", route: "notes.home" }] },
        { id: "ops", routes: [{ name: "ops.home", path: "/ops", component: EmptyPage }], menus: [{ id: "ops", route: "ops.home" }] },
      ], { console: { chrome: TestChrome, requireAuth: true } }),
      schemas: {
        public: { url: "https://example.test/graphql/public/", fetch },
        console: { url: "https://example.test/graphql/console/", fetch },
      },
    });
    expect(app.explain.home).toBe("/notes");
    const host = document.createElement("div");
    document.body.append(host);
    const length = history.length;
    const root = app.mount(host);
    try {
      await waitFor(() => expect(window.location.pathname).toBe(lands));
      expect(history.length).toBe(length);
    } finally {
      root.unmount();
      host.remove();
    }
  });
});

describe("createApp route menu refs", () => {
  test("resolves menu route refs and exposes refine breadcrumbs", async () => {
    const menus: readonly ChromeMenuItem[] = [
      {
        id: "admin",
        label: "Admin",
        icon: "auth",
        route: "admin.home",
        children: [
          {
            id: "admin.users",
            label: "Users",
            route: "admin.users",
            icon: "users",
          },
        ],
      },
    ];

    const captured = await captureChrome({
      path: "/admin/users",
      addons: [
        {
          id: "admin",
          routes: [
            {
              name: "admin.home",
              path: "/admin",
              layout: "console",
              component: EmptyPage,
            },
            {
              name: "admin.users",
              path: "/admin/users",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus,
        },
      ],
    });

    try {
      expect(chromeSnapshot(captured.props())).toEqual({
        breadcrumbs: [
          { label: "Admin", to: "/admin" },
          { label: "Users", to: "/admin/users" },
        ],
      });
      expect(
        captured.props().menus[0]?.children?.[0]?.to,
      ).toBe("/admin/users");
    } finally {
      captured.cleanup();
    }
  });

  test("projects menu route record children into refine breadcrumbs", async () => {
    const captured = await captureChrome({
      path: "/files/file-1",
      addons: [
        {
          id: "files",
          routes: [
            {
              name: "files.home",
              path: "/files",
              layout: "console",
              component: EmptyPage,
            },
            {
              name: "files.record",
              path: "/files/$id",
              layout: "console",
              parent: "files.home",
            },
          ],
          menus: [
            {
              id: "files",
              label: "Files",
              route: "files.home",
              icon: "files",
            },
          ],
        },
      ],
    });

    try {
      expect(chromeSnapshot(captured.props())).toEqual({
        breadcrumbs: [
          { label: "Files", to: "/files" },
          { label: "Show" },
        ],
      });
    } finally {
      captured.cleanup();
    }
  });

  test("collapses route-less menu groups that duplicate their leaf crumb", async () => {
    const menus: readonly ChromeMenuItem[] = [
      {
        id: "admin",
        label: "Admin",
        icon: "auth",
        route: "admin.home",
        children: [
          {
            id: "admin.users.group",
            label: "Users",
            children: [
              {
                id: "admin.users",
                label: "Users",
                route: "admin.users",
                icon: "users",
              },
            ],
          },
        ],
      },
    ];

    const captured = await captureChrome({
      path: "/admin/users",
      addons: [
        {
          id: "admin",
          routes: [
            {
              name: "admin.home",
              path: "/admin",
              layout: "console",
              component: EmptyPage,
            },
            {
              name: "admin.users",
              path: "/admin/users",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus,
        },
      ],
    });

    try {
      expect(chromeSnapshot(captured.props())).toEqual({
        breadcrumbs: [
          { label: "Admin", to: "/admin" },
          { label: "Users", to: "/admin/users" },
        ],
      });
    } finally {
      captured.cleanup();
    }
  });

  test("keeps repeated crumbs under their authored root so they do not become rail apps", async () => {
    const captured = await captureChrome({
      path: "/agents",
      addons: [
        {
          id: "agents",
          routes: [
            {
              name: "agents.home",
              path: "/agents",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [
            {
              id: "agents",
              label: "Agents",
              children: [
                {
                  id: "agents.group",
                  label: "Agents",
                  children: [
                    {
                      id: "agents.home",
                      label: "Agents",
                      route: "agents.home",
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    });

    try {
      const tree = MenuTree.from(captured.props().menus);

      expect(tree.railMenuItems().map((item) => item.id)).toEqual(["agents"]);
      expect(tree.trailFor("agents.home").map((item) => item.id)).toEqual([
        "agents",
        "agents.group",
        "agents.home",
      ]);
      expect(tree.activeAppRoot("/agents")?.targetedChildren.map((item) => item.id)).toEqual([
        "agents.group",
      ]);
    } finally {
      captured.cleanup();
    }
  });

  test("preserves same-label grouped app menus for pane-tree rendering", async () => {
    const captured = await captureChrome({
      path: "/agents/skills",
      addons: [
        {
          id: "agents",
          routes: [
            {
              name: "agents.home",
              path: "/agents",
              layout: "console",
              component: EmptyPage,
            },
            {
              name: "agents.skills",
              path: "/agents/skills",
              layout: "console",
              component: EmptyPage,
            },
            {
              name: "agents.sources",
              path: "/agents/sources",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [
            {
              id: "agents",
              label: "Agents",
              children: [
                {
                  id: "agents.group",
                  label: "Agents",
                  children: [
                    {
                      id: "agents.home",
                      label: "Agents",
                      route: "agents.home",
                    },
                  ],
                },
                {
                  id: "agents.skills.group",
                  label: "Skills",
                  children: [
                    {
                      id: "agents.skills",
                      label: "Skills",
                      route: "agents.skills",
                    },
                    {
                      id: "agents.sources",
                      label: "Sources",
                      route: "agents.sources",
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    });

    try {
      const tree = MenuTree.from(captured.props().menus);

      expect(tree.roots.map((item) => item.id)).toEqual(["agents"]);
      expect(tree.activeAppRoot("/agents/skills")?.targetedChildren.map((item) => item.id)).toEqual([
        "agents.group",
        "agents.skills.group",
      ]);
      expect(tree.trailFor("agents.skills").map((item) => item.id)).toEqual([
        "agents",
        "agents.skills.group",
        "agents.skills",
      ]);
      expect(chromeSnapshot(captured.props())).toEqual({
        breadcrumbs: [
          { label: "Agents", to: "/agents" },
          { label: "Skills", to: "/agents/skills" },
        ],
      });
    } finally {
      captured.cleanup();
    }
  });

  test("keeps authored menu order when schema resources attach under menu parents", async () => {
    const captured = await captureChrome({
      path: "/agents",
      schemas: testSchemasWithConsoleResources([
        testDataResource("agents.MCPServer"),
        testDataResource("agents.InferenceProvider"),
        testDataResource("agents.Agent"),
        testDataResource("agents.Skill"),
      ]),
      addons: [
        {
          id: "agents",
          routes: [
            {
              name: "agents.agents",
              path: "/agents",
              layout: "console",
              component: EmptyPage,
              resource: "agents.Agent",
            },
            {
              name: "agents.skills",
              path: "/agents/skills",
              layout: "console",
              component: EmptyPage,
              resource: "agents.Skill",
            },
            {
              name: "agents.mcp",
              path: "/agents/mcp",
              layout: "console",
              component: EmptyPage,
              resource: "agents.MCPServer",
            },
            {
              name: "agents.inference",
              path: "/agents/inference",
              layout: "console",
              component: EmptyPage,
              resource: "agents.InferenceProvider",
            },
          ],
          menus: [
            {
              id: "agents",
              label: "Agents",
              children: [
                {
                  id: "agents.menu.agents",
                  label: "Agents",
                  children: [
                    {
                      id: "agents.agents",
                      label: "Agents",
                      route: "agents.agents",
                    },
                  ],
                },
                {
                  id: "agents.menu.skills",
                  label: "Skills",
                  children: [
                    {
                      id: "agents.skills",
                      label: "Skills",
                      route: "agents.skills",
                    },
                  ],
                },
                {
                  id: "agents.menu.mcp",
                  label: "MCP",
                  children: [
                    {
                      id: "agents.mcp",
                      label: "MCP",
                      route: "agents.mcp",
                    },
                  ],
                },
                {
                  id: "agents.menu.inference",
                  label: "Inference",
                  children: [
                    {
                      id: "agents.inference",
                      label: "Inference",
                      route: "agents.inference",
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    });

    try {
      const tree = MenuTree.from(captured.props().menus);

      expect(tree.activeAppRoot("/agents")?.targetedChildren.map((item) => item.id)).toEqual([
        "agents.menu.agents",
        "agents.menu.skills",
        "agents.menu.mcp",
        "agents.menu.inference",
      ]);
    } finally {
      captured.cleanup();
    }
  });

  test("rejects a menu item that declares both route and to", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "bad-menu",
          routes: [
            {
              name: "bad.home",
              path: "/bad",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [{ id: "bad", route: "bad.home", to: "/bad" }],
        },
      ])),
    ).toThrow(/declares both route and to/);
  });

  test("allows multiple menu refs without route-chrome ambiguity", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "explicit",
          routes: [
            {
              name: "explicit.home",
              path: "/explicit",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [
            { id: "explicit.a", label: "Explicit A", route: "explicit.home" },
            { id: "explicit.b", label: "Explicit B", route: "explicit.home" },
          ],
        },
      ])),
    ).not.toThrow();
  });

  test("requires resource route.menu to select one of the route's menu refs when refs exist", () => {
    expect(() =>
      createAppWithResources([
        {
          id: "wrong-menu",
          routes: [
            {
              name: "wrong.home",
              path: "/wrong",
              layout: "console",
              menu: "wrong.other",
              resource: "Wrong",
              component: EmptyPage,
            },
          ],
          menus: [
            { id: "wrong.home", label: "Wrong", route: "wrong.home" },
            { id: "wrong.other", label: "Other" },
          ],
        },
      ], [testDataResource("example.Wrong")]),
    ).toThrow(/does not reference the route/);
  });

  test("rejects a menu item that references an unknown route", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "unknown-route",
          routes: [
            {
              name: "known.home",
              path: "/known",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [{ id: "missing", route: "missing.home" }],
        },
      ])),
    ).toThrow(/references unknown route "missing.home"/);
  });

  test("identifies the menu item when its route requires params", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "record-menu",
          routes: [
            {
              name: "records.record",
              path: "/records/$id",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [{ id: "records.open", route: "records.record" }],
        },
      ])),
    ).toThrow(/Menu item "records\.open" cannot resolve its route.*missing params: id/);
  });

  test("resolves a menu route with its declared params", async () => {
    const captured = await captureChrome({
      path: "/dashboards/addon/example.document_review.overview",
      addons: [
        {
          id: "dashboard-menu",
          routes: [
            {
              name: "dashboards.addon",
              path: "/dashboards/addon/$key",
              layout: "console",
              component: EmptyPage,
            },
          ],
          menus: [
            {
              id: "review-queue",
              route: "dashboards.addon",
              params: { key: "example.document_review.overview" },
            },
          ],
        },
      ],
    });

    try {
      expect(captured.props().menus[0]?.to).toBe(
        "/dashboards/addon/example.document_review.overview",
      );
    } finally {
      captured.cleanup();
    }
  });

  test("keeps route.menu when another menu item names one destination of the parameterized route", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "dashboard-placement",
          routes: [
            {
              name: "dashboards.index",
              path: "/dashboards",
              layout: "console",
              component: EmptyPage,
            },
            {
              name: "dashboards.addon",
              path: "/dashboards/addon/$key",
              layout: "console",
              menu: "dashboards",
              component: EmptyPage,
            },
          ],
          menus: [
            { id: "dashboards", route: "dashboards.index" },
            {
              id: "review-queue",
              route: "dashboards.addon",
              params: { key: "example.document_review.overview" },
            },
          ],
        },
      ])),
    ).not.toThrow();
  });

  test("rejects internal literal menu targets", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "literal-menu",
          menus: [{ id: "literal", to: "/dashboards/addon/literal" }],
        },
      ])),
    ).toThrow(/declares internal target.*use route and params/);
  });

  test("rejects a route that references an unknown menu item", () => {
    expect(() =>
      createAppWithResources([
        {
          id: "unknown-menu",
          routes: [
            {
              name: "known.home",
              path: "/known",
              layout: "console",
              menu: "missing-menu",
              resource: "Known",
              component: EmptyPage,
            },
          ],
        },
      ], [testDataResource("example.Known")]),
    ).toThrow(/references unknown menu item "missing-menu"/);
  });
});

describe("createApp resource route index", () => {
  test("exposes a resource collection path through useResourceRoute", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/clients");

    function ClientsProbe(): ReactNode {
      const path = useResourceRoute("OAuthClient");
      const recordHref = useResourceRecordHrefLookup();
      const routeHref = useRouteHref();
      return createElement(
        "span",
        null,
        `route ${path ?? "none"} record ${routeHref("clients.record", { id: "client 1" })} ` +
          `dynamic ${recordHref("OAuthClient", "client 2") ?? "none"}`,
      );
    }

    const app = createAppWithResources([
      {
        id: "clients",
        routes: [
          {
            name: "clients.home",
            path: "/clients",
            layout: "console",
            component: ClientsProbe,
            resource: "OAuthClient",
          },
          {
            name: "clients.record",
            path: "/clients/$id",
            layout: "console",
            parent: "clients.home",
          },
        ],
      },
    ], [testDataResource("integrate.OAuthClient")]);
    const root = app.mount(host);

    try {
      await waitFor(() => {
        expect(host.textContent).toContain(
          "route /clients record /clients/client%201",
        );
        expect(host.textContent).toContain("dynamic /clients/client%202");
      });
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("rejects two routes claiming the same resource", () => {
    expect(() =>
      createAppWithResources([
        {
          id: "dup-model",
          routes: [
            {
              name: "a.home",
              path: "/a",
              layout: "console",
              component: EmptyPage,
              resource: "OAuthClient",
            },
            {
              name: "b.home",
              path: "/b",
              layout: "console",
              component: EmptyPage,
              resource: "integrate.OAuthClient",
            },
          ],
        },
      ], [testDataResource("integrate.OAuthClient")]),
    ).toThrow(/claims resource "integrate\.OAuthClient"/);
  });

  test("rejects an unknown route resource during composition", () => {
    expect(() =>
      createAppWithResources([
        {
          id: "unknown-model",
          routes: [
            {
              name: "missing.home",
              path: "/missing",
              layout: "console",
              component: EmptyPage,
              resource: "Missing",
            },
          ],
        },
      ], [testDataResource("notes.Note")]),
    ).toThrow(/Unknown model spelling "Missing"/);
  });

  test("reports absent schema resources distinctly during composition", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "notes",
          routes: [
            {
              name: "notes.home",
              path: "/notes",
              layout: "console",
              component: EmptyPage,
              resource: "notes.Note",
            },
          ],
        },
      ])),
    ).toThrow(/schema metadata exposes no resources/);
  });

  test("exposes inherited model and canonical labels on chatter record routes", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/notes/abc");

    function ChatterRouteProbe(): ReactNode {
      const route = useChatterRoutes().find((item) => item.name === "notes.record");
      const tabs = useContainer("record#aside").map((child) => child.id);
      return createElement(
        "span",
        null,
        `${route?.modelLabel ?? "none"} ${route?.canonicalLabel ?? "none"} ${route?.recordParam ?? "none"} ${tabs.join(",") || "none"}`,
      );
    }

    const input = testAppInput([
      {
        id: "notes",
        menus: [{ id: "notes", route: "notes.home" }],
        containers: { "record#aside": [{ only: ["chatter.comments"], when: { route: "notes.home" } }] },
        routes: [
          {
            name: "notes.home",
            path: "/notes",
            layout: "console",
            component: ChatterRouteProbe,
            resource: "Note",
          },
          {
            name: "notes.record",
            path: "/notes/$id",
            layout: "console",
            parent: "notes.home",
          },
        ],
      },
    ]);
    input.schemas = testSchemasWithConsoleResources([
      { ...testDataResource("notes.Note"), canonicalLabel: "parties.Party" },
    ]);
    const app = createApp(input);
    const root = app.mount(host);

    try {
      await waitFor(() => {
        expect(host.textContent).toContain("notes.Note parties.Party id chatter.comments");
      });
    } finally {
      root.unmount();
      host.remove();
    }
  });
});

describe("createApp route tree", () => {
  test("renders addon-contributed status tones through the app runtime", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/review");
    const app = createApp(testAppInput([{
      id: "review",
      statusTones: { reviewed: "accent" },
      routes: [{
        name: "review", path: "/review",
        component: () => createElement(statusBadgeWidget.read, { value: "REVIEWED" }),
      }],
    }]));
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.querySelector(".bg-accent-soft")?.textContent).toBe("Reviewed"));
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("keeps a contributed layout provider mounted when changing pages", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/first");
    let mounts = 0;
    function Provider({ children }: RefineLayoutChromeProps): ReactNode {
      useEffect(() => { mounts += 1; }, []);
      return children;
    }
    const app = createApp(testAppInput([{
      id: "pages",
      layoutProviders: [{ id: "session", layout: "console", component: Provider }],
      routes: [
        { name: "first", path: "/first", component: () => createElement("p", null, "First page") },
        { name: "second", path: "/second", component: () => createElement("p", null, "Second page") },
      ],
    }]));
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain("First page"));
      const initialMounts = mounts;
      expect(initialMounts).toBeGreaterThan(0);
      await app.router.navigate({ to: "/second" });
      await waitFor(() => expect(host.textContent).toContain("Second page"));
      expect(mounts).toBe(initialMounts);
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("while a code-split page loads, the previous page's band leaves sight and reach and the page skeleton holds the row", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/first");
    let releaseChunk!: () => void;
    const chunk = new Promise<void>((resolve) => { releaseChunk = resolve; });
    const app = createApp(testAppInput([{ id: "pages", routes: [
      { name: "first", path: "/first", component: () => createElement("div", null,
        createElement(ControlBand, null, createElement("button", { type: "button" }, "Group by")),
        createElement("p", null, "First page")) },
      { name: "second", path: "/second", component: lazyRouteComponent(async () => {
        await chunk;
        return { SecondPage: () => createElement("p", null, "Second page") };
      }, "SecondPage") },
    ] }], { console: { chrome: BandChrome, requireAuth: false } }));
    const root = app.mount(host);
    try {
      await within(host).findByRole("button", { name: "Group by" });
      void app.router.navigate({ to: "/second" });
      const pending = await within(host).findByRole("status", {}, { timeout: 3000 });
      expect(pending.textContent).toContain("Loading…");
      expect(within(host).queryByRole("button", { name: "Group by" })).toBeNull();
      const visibleBands = [...within(host).getByTestId("band-host").children]
        .filter((band) => (band as HTMLElement).style.display !== "none");
      expect(visibleBands).toHaveLength(1);
      expect(visibleBands[0]?.textContent).toBe("");
      releaseChunk();
      await waitFor(() => expect(host.textContent).toContain("Second page"));
      expect(within(host).getByTestId("band-host").children).toHaveLength(0);
    } finally {
      releaseChunk();
      root.unmount();
      host.remove();
    }
  });

  test("an intent preload follows / to its home without navigating or writing session state", async () => {
    window.sessionStorage.clear();
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/first?debug=1");
    const loadHome = vi.fn(async () => ({ HomePage: () => createElement("p", null, "Home page") }));
    const app = createApp(testAppInput([{ id: "pages", routes: [
      { name: "first", path: "/first", component: () => createElement("p", null, "First page") },
      { name: "home", path: "/home", component: lazyRouteComponent(loadHome, "HomePage") },
    ], menus: [{ id: "home", route: "home" }] }]));
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain("First page"));
      expect(window.sessionStorage.getItem("angee:developer-mode")).toBe("1");
      // The user menu switched developer mode off; the URL still says ?debug=1.
      window.sessionStorage.setItem("angee:developer-mode", "0");
      // The router follows the redirect inside one preload, which resolves to the landing page's matches.
      const matches = await app.router.preloadRoute({ to: "/" });
      expect(matches?.at(-1)?.pathname).toBe("/home");
      expect(loadHome).toHaveBeenCalledOnce();
      expect(app.router.state.location.href).toBe("/first?debug=1");
      expect(window.sessionStorage.getItem("angee:developer-mode")).toBe("0");
    } finally {
      root.unmount();
      host.remove();
      window.sessionStorage.clear();
    }
  });

  test.each([
    { label: "fallback", preferences: {}, target: "/first" },
    { label: "homePath", preferences: { [HOME_PATH_PREFERENCE_KEY]: "/second?tab=activity#details" }, target: "/second?tab=activity#details" },
    { label: "default app", preferences: { "chrome.rail": { defaultItemId: "second" } }, target: "/second" },
    { label: "fallback past a stale default app", preferences: { "chrome.rail": { defaultItemId: "removed" } }, target: "/first" },
  ])("redirects / to the $label without remounting the layout or leaving / in history", async ({ preferences, target }) => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/first");
    let mounts = 0;
    function Provider({ children }: RefineLayoutChromeProps): ReactNode {
      useEffect(() => { mounts += 1; }, []);
      return children;
    }
    const identityFetch: typeof fetch = async () => Response.json({ data: {
      current_user: { id: "user-1", username: "user", firstName: "", lastName: "", roleRefs: [], preferences },
      real_user: null, viewable_people: [],
    } });
    const app = createApp({
      ...testAppInput([{
        id: "pages",
        layoutProviders: [{ id: "session", layout: "console", component: Provider }],
        routes: [
          { name: "first", path: "/first", component: () => createElement("p", null, "First page") },
          { name: "second", path: "/second", component: () => createElement("p", null, "Second page") },
          { name: "settings", path: "/settings", component: () => createElement("p", null, "Settings page") },
        ],
        menus: [{ id: "first", route: "first" }, { id: "second", route: "second" }],
      }], { console: { chrome: TestChrome, requireAuth: true } }),
      schemas: {
        public: { ...TEST_SCHEMAS.public, fetch: identityFetch },
        console: { ...TEST_SCHEMAS.console, fetch: identityFetch },
      },
    });
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain("First page"));
      await app.router.navigate({ to: "/settings" });
      await waitFor(() => expect(host.textContent).toContain("Settings page"));
      const initialMounts = mounts;
      const historyLength = app.router.history.length;
      await app.router.navigate({ to: "/" });
      await waitFor(() => expect(app.router.state.location.href).toBe(target));
      await waitFor(() => expect(host.textContent).toContain(target.startsWith("/first") ? "First page" : "Second page"));
      expect(mounts).toBe(initialMounts);
      expect(app.router.history.length).toBe(historyLength + 1);
      app.router.history.back();
      await waitFor(() => expect(app.router.state.location.pathname).toBe("/settings"));
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test.each([
    { label: "homePath", preferences: { [HOME_PATH_PREFERENCE_KEY]: "/third" } },
    { label: "default app", preferences: { "chrome.rail": { defaultItemId: "desk" } } },
  ])("a selected app sends / to its own home and ignores the $label preference", async ({ preferences }) => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/");
    const identityFetch: typeof fetch = async () => Response.json({ data: {
      current_user: { id: "user-1", username: "user", firstName: "", lastName: "", roleRefs: [], preferences },
      real_user: null, viewable_people: [],
    } });
    const page = (name: string) => ({ name, path: `/${name}`, component: () => createElement("p", null, `${name} page`) });
    const app = createApp({
      ...testAppInput([{
        id: "pages",
        routes: [page("first"), page("second"), page("third")],
        menus: [{ id: "desk", home: "second", children: [{ id: "first", route: "first" }, { id: "second", route: "second" }, { id: "third", route: "third" }] }],
      }]),
      location: { search: "?app=desk" },
      schemas: {
        public: { ...TEST_SCHEMAS.public, fetch: identityFetch },
        console: { ...TEST_SCHEMAS.console, fetch: identityFetch },
      },
    });
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain("second page"));
      expect(app.router.state.location.href).toBe("/second");
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("a cold / waits for identity and honours homePath before rendering the fallback", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/");
    let releaseIdentity!: () => void;
    const identityReady = new Promise<void>((resolve) => { releaseIdentity = resolve; });
    let identityRequested = false;
    let fallbackRendered = false;
    const identityFetch: typeof fetch = async () => {
      identityRequested = true;
      await identityReady;
      return Response.json({ data: {
        current_user: { id: "user-1", username: "user", firstName: "", lastName: "", roleRefs: [],
          preferences: { [HOME_PATH_PREFERENCE_KEY]: "/preferred?tab=activity#details" } },
        real_user: null, viewable_people: [],
      } });
    };
    const app = createApp({
      ...testAppInput([{ id: "pages", routes: [
        { name: "fallback", path: "/fallback", component: () => { fallbackRendered = true; return null; } },
        { name: "preferred", path: "/preferred", component: () => createElement("p", null, "Preferred page") },
      ] }]),
      schemas: {
        public: { ...TEST_SCHEMAS.public, fetch: identityFetch },
        console: { ...TEST_SCHEMAS.console, fetch: identityFetch },
      },
    });
    const root = app.mount(host);
    try {
      const historyLength = app.router.history.length;
      await waitFor(() => expect(identityRequested).toBe(true));
      expect(fallbackRendered).toBe(false);
      releaseIdentity();
      await waitFor(() => expect(host.textContent).toContain("Preferred page"));
      expect(app.router.state.location.href).toBe("/preferred?tab=activity#details");
      expect(fallbackRendered).toBe(false);
      expect(app.router.history.length).toBe(historyLength);
    } finally {
      releaseIdentity();
      root.unmount();
      host.remove();
    }
  });

  test("a cold / still sends an authoritative 401 to the protected home's sign-in", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/");
    const unauthorizedFetch: typeof fetch = async () => Response.json({ errors: [{ message: "Authentication required" }] }, { status: 401 });
    const app = createApp({
      ...testAppInput([{ id: "pages", routes: [
        { name: "private", path: "/private", component: () => createElement("p", null, "Private page") },
        { name: "sign-in", path: "/sign-in", layout: "public", component: () => createElement("p", null, "Sign-in page") },
      ] }], {
        console: { chrome: TestChrome, requireAuth: true },
        public: { chrome: TestChrome, requireAuth: false, schema: "public" },
      }),
      loginPath: "/sign-in",
      schemas: {
        public: { ...TEST_SCHEMAS.public, fetch: unauthorizedFetch },
        console: { ...TEST_SCHEMAS.console, fetch: unauthorizedFetch },
      },
    });
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain("Sign-in page"));
      expect(app.router.state.location.pathname).toBe("/sign-in");
      // Back to `/` after sign-in, so the landing rule runs with the person's preferences.
      expect(app.router.state.location.search).toEqual({ next: "/" });
      expect(host.textContent).not.toContain("Private page");
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("a cold / uses the session retry surface after a transient identity failure", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/");
    let requests = 0;
    const identityFetch: typeof fetch = async () => {
      requests += 1;
      if (requests === 1) throw new Error("Transient identity failure");
      return testGraphQLFetch();
    };
    const app = createApp({
      ...testAppInput([{ id: "pages", routes: [
        { name: "home", path: "/home", component: () => createElement("p", null, "Home page") },
      ] }]),
      schemas: {
        public: { ...TEST_SCHEMAS.public, fetch: identityFetch },
        console: { ...TEST_SCHEMAS.console, fetch: identityFetch },
      },
    });
    const root = app.mount(host);
    try {
      await within(host).findByText("Unable to check your session");
      expect(host.textContent).not.toContain("Transient identity failure");
      expect(app.router.state.location.pathname).toBe("/");
      fireEvent.click(within(host).getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(host.textContent).toContain("Home page"));
      expect(app.router.state.location.pathname).toBe("/home");
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("mounts an explicit record page and returns to its list index", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/services/django");
    const app = createApp(testAppInput([{
      id: "services",
      routes: resourcePageRoutes(
        "services", "/services",
        () => createElement("p", null, "Service list"),
        undefined,
        { detailComponent: () => createElement("p", null, "Service detail") },
      ),
    }]));
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain("Service detail"));
      expect(host.textContent).not.toContain("Service list");
      await app.router.navigate({ to: "/services" });
      await waitFor(() => expect(host.textContent).toContain("Service list"));
      expect(host.textContent).not.toContain("Service detail");
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("nests addon routes under layouts and declared parents", () => {
    const app = createApp(testAppInput([
      {
        id: "notes",
        routes: [
          {
            name: "notes.home",
            path: "/notes",
            layout: "console",
            component: EmptyPage,
          },
          {
            name: "notes.record",
            path: "/notes/$id",
            layout: "console",
            parent: "notes.home",
          },
        ],
      },
    ]));
    const routes = routesByFullPath(app.router);
    const layout = layoutRoute(app.router, "console");
    const home = routes.get("/notes");
    const record = routes.get("/notes/$id");

    expect(layout).toBeTruthy();
    expect(home?.parentRoute).toBe(layout);
    expect(record?.parentRoute).toBe(home);
  });

  test("lets child params reach the parent route surface", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/notes/first");

    function NotePageProbe(): ReactNode {
      const params = useParams({ strict: false }) as { id?: string };
      return createElement("span", null, `Note id ${params.id ?? ""}`);
    }

    const app = createApp(testAppInput([
      {
        id: "notes",
        routes: [
          {
            name: "notes.home",
            path: "/notes",
            layout: "console",
            component: NotePageProbe,
          },
          {
            name: "notes.record",
            path: "/notes/$id",
            layout: "console",
            parent: "notes.home",
          },
        ],
      },
    ]));
    const root = app.mount(host);

    try {
      await waitFor(() => {
        expect(host.textContent).toContain("Note id first");
      });
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("rejects a route with an unknown parent", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "bad-parent",
          routes: [
            {
              name: "child",
              path: "/child",
              layout: "console",
              parent: "missing",
            },
          ],
        },
      ])),
    ).toThrow(/references unknown parent route "missing"/);
  });

  test("uses the declared parent route instead of revalidating layout strings", () => {
    const app = createApp(testAppInput([
      {
        id: "cross-layout",
        routes: [
          {
            name: "public.parent",
            path: "/public",
            layout: "public",
            component: EmptyPage,
          },
          {
            name: "console.child",
            path: "/public/child",
            layout: "console",
            parent: "public.parent",
          },
        ],
      },
    ], {
      console: { chrome: TestChrome, requireAuth: false },
      public: { chrome: TestChrome, requireAuth: false },
    }));
    const routes = routesByFullPath(app.router);

    expect(routes.get("/public/child")?.parentRoute).toBe(
      routes.get("/public"),
    );
  });

  test("nests child route paths under the declared parent route", () => {
    const app = createApp(testAppInput([
      {
        id: "bad-prefix",
        routes: [
          {
            name: "notes.home",
            path: "/notes",
            layout: "console",
            component: EmptyPage,
          },
          {
            name: "notes.record",
            path: "/not-notes/$id",
            layout: "console",
            parent: "notes.home",
          },
        ],
      },
    ]));
    const routes = routesByFullPath(app.router);

    expect(routes.get("/notes/not-notes/$id")?.parentRoute).toBe(
      routes.get("/notes"),
    );
    expect(routes.has("/not-notes/$id")).toBe(false);
  });

  test("allows path-only route declarations", () => {
    const app = createApp(testAppInput([
      {
        id: "missing-component",
        routes: [
          {
            name: "empty.home",
            path: "/empty",
            layout: "console",
          },
        ],
      },
    ]));
    const routes = routesByFullPath(app.router);
    const layout = layoutRoute(app.router, "console");

    expect(routes.get("/empty")?.parentRoute).toBe(layout);
  });

  test("rejects a route that references an undeclared layout", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "bad-layout",
          routes: [
            {
              name: "bad.home",
              path: "/bad",
              layout: "missing",
              component: EmptyPage,
            },
          ],
        },
      ])),
    ).toThrow(/references undeclared layout "missing"/);
  });
});

function TestChrome({ children }: RefineLayoutChromeProps): ReactNode {
  return children;
}

/** A layout's control band row, as ConsoleLayout hosts it, above the routed page. */
function BandChrome({ children }: RefineLayoutChromeProps): ReactNode {
  const [bandHost, setBandHost] = useState<HTMLDivElement | null>(null);
  return createElement(ControlBandProvider, { host: bandHost, children: createElement(Fragment, null,
    createElement("div", { "data-testid": "band-host", ref: setBandHost }),
    children) });
}

function EmptyPage(): ReactNode {
  return null;
}

function testAppInput(
  addons: readonly BaseAddon[],
  layouts: Parameters<typeof createApp>[0]["layouts"] = {
    console: { chrome: TestChrome, requireAuth: false },
  },
): Parameters<typeof createApp>[0] {
  return {
    addons,
    layouts,
    schemas: TEST_SCHEMAS,
    defaultSchema: "console",
    subscriptionSchema: "console",
    // Nothing selected unless a test says so, whatever URL an earlier test left.
    location: { search: "" },
  };
}

function testSchemasWithConsoleResources(
  resources: readonly DataResourceMetadata[],
): CreateAppInput["schemas"] {
  return {
    ...TEST_SCHEMAS,
    console: {
      ...TEST_SCHEMAS.console,
      metadata: { angee: { resources } },
    },
  };
}

function createAppWithResources(
  addons: readonly BaseAddon[],
  resources: readonly DataResourceMetadata[],
) {
  const input = testAppInput(addons);
  input.schemas = testSchemasWithConsoleResources(resources);
  return createApp(input);
}

function routesByFullPath(router: unknown): Map<string, TestRoute> {
  const routes = Object.values((router as TestRouter).routesById);
  return new Map(routes.map((route) => [route.fullPath, route]));
}

function layoutRoute(router: unknown, layout: string): TestRoute | undefined {
  return Object.values((router as TestRouter).routesById).find((route) =>
    route.id.endsWith(`_angee_layout_${layout}`),
  );
}

interface TestRouter {
  routesById: Record<string, TestRoute>;
}

interface TestRoute {
  id: string;
  fullPath: string;
  parentRoute?: TestRoute;
}

function probeFetch(
  schema: string,
  seen: Record<string, string>,
): typeof fetch {
  return async (input, init) => {
    const url = requestUrl(input);
    const body =
      typeof init?.body === "string"
        ? init.body
        : input instanceof Request
          ? await input.clone().text()
          : "";
    if (`${decodeURIComponent(url)} ${body}`.includes(`${titleCase(schema)}Probe`)) {
      seen[schema] = url;
    }
    return new Response(
      JSON.stringify({
        data: { __typename: "Query", current_user: null, schemaProbe: schema },
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  };
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

function titleCase(value: string): string {
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}


test("unknown and incompatible default views fail at composition", () => {
  const resources = [testDataResource("notes.Note"), testDataResource("teams.Team")];
  const preset = { id: "desk.open", label: "Open", resource: "notes.Note" };
  const input = (resource: string, id: string, menu = false): CreateAppInput => ({
    ...testAppInput([{
      id: "desk", resourceViews: [preset],
      routes: resourcePageRoutes("desk.all", "/desk", EmptyPage, resource,
        menu ? {} : { defaultResourceView: id }),
      menus: [{ id: "desk", route: "desk.all", ...(menu ? { defaultResourceView: id } : {}) }],
    }]),
    schemas: testSchemasWithConsoleResources(resources),
  });
  expect(() => createApp(input("notes.Note", preset.id))).not.toThrow();
  expect(() => createApp(input("notes.Note", "desk.missing"))).toThrow(/default resource view/);
  expect(() => createApp(input("teams.Team", preset.id))).toThrow(/incompatible/);
  expect(() => createApp(input("teams.Team", preset.id, true))).toThrow(/route "desk.all" does not admit/);
});

test("a menu preset is admitted on its target route beside the route default", async () => {
  let admitted: readonly string[] | undefined;
  function Probe(): ReactNode {
    admitted = useAppRuntime().menuResourceViewIds;
    return createElement("span", null, "Menu preset probe");
  }
  const app = createApp({
    ...testAppInput([{
      id: "desk",
      resourceViews: [
        { id: "desk.open", label: "Open", resource: "notes.Note" },
        { id: "desk.archived", label: "Archived", resource: "notes.Note" },
      ],
      routes: resourcePageRoutes("desk.all", "/desk", Probe, "notes.Note", { defaultResourceView: "desk.open" }),
      menus: [{ id: "desk", route: "desk.all", defaultResourceView: "desk.archived" }],
    }], { console: { requireAuth: false } }),
    schemas: testSchemasWithConsoleResources([testDataResource("notes.Note")]),
  });
  const originalHref = `${location.pathname}${location.search}${location.hash}`;
  history.replaceState(null, "", "/desk?preset=desk.archived");
  const host = document.createElement("div");
  document.body.append(host);
  const root = app.mount(host);
  try {
    await waitFor(() => expect(admitted).toEqual(["desk.archived"]));
  } finally {
    root.unmount();
    host.remove();
    history.replaceState(null, "", originalHref);
  }
});

test("a selected app's links, vocabulary and Settings follow one projection; pages outside its rail stay reachable in its home app", async () => {
  const record = testDataResource("records.Record", {
    fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
      aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  });
  let observed: { href: string; lookup?: string; collection?: string; title: string; field?: string; label?: string; preset?: string; menu?: string } | undefined;
  function Probe(): ReactNode {
    const lookup = useResourceRecordHrefLookup();
    const collection = useResourceRoute("records.Record");
    const t = useT("records");
    const model = useModelMetadata("records.Record");
    const runtime = useAppRuntime();
    const menu = useChromeMenuTree().byId.get("desk.review")?.displayLabel;
    observed = { href: lookup("records.Record", "r/2") ?? "", lookup: lookup("records.Record", "r/2"), collection,
      title: t("title"), field: model?.fields.title?.label, label: model?.label, preset: runtime.defaultResourceView, menu };
    return createElement("span", null, "Projection probe");
  }
  const addon: BaseAddon = {
    id: "desk",
    i18n: { records: { title: "Records" } },
    routes: [
      ...resourcePageRoutes("records.all", "/records", Probe, "records.Record"),
      ...resourcePageRoutes("teams.all", "/teams", Probe, "teams.Team"),
      ...resourcePageRoutes("desk.incoming", "/desk/incoming", Probe, "records.Record", { defaultResourceView: "desk.open" }),
      ...resourcePageRoutes("desk.review", "/desk/review", Probe, undefined, { recordModel: "records.Record" }),
    ],
    menus: [
      { id: "records", route: "records.all" },
      { id: "teams", route: "teams.all" },
      { id: "desk", home: "desk.incoming", children: [
        { id: "desk.incoming", route: "desk.incoming" },
        { id: "desk.review", route: "desk.review" },
        { id: "desk.settings", group: "platform", children: [
          { id: "desk.team", route: "teams.all.record", params: { id: "team-1" } },
        ] },
      ] },
    ],
    vocabulary: [
      { app: "desk", messages: { records: { title: "Incoming" } }, resources: { "records.Record": { label: "Request", fields: { title: "Subject" } } }, menus: { "desk.review": "Review queue" } },
      { app: "desk", route: "desk.review", messages: { records: { title: "Reviews" } }, resources: { "records.Record": { label: "Review", fields: { title: "Question" } } }, menus: { "desk.review": "Questions" } },
    ],
    resourceViews: [{ id: "desk.open", label: "Open records", resource: "records.Record", pageSize: 20 }],
  };
  history.replaceState(null, "", "/desk/incoming/r1");
  const app = createApp({
    ...testAppInput([addon], { console: { requireAuth: false } }),
    schemas: testSchemasWithConsoleResources([record, testDataResource("teams.Team")]),
    location: { search: "?app=desk" },
  });
  expect(app.explain.selection).toMatchObject({ app: "desk", rail: ["desk"], home: "desk.incoming" });
  const host = document.createElement("div");
  document.body.append(host);
  const root = app.mount(host);
  try {
    await waitFor(() => expect(observed).toMatchObject({ href: "/desk/incoming/r%2F2", collection: "/desk/incoming", lookup: "/desk/incoming/r%2F2", title: "Incoming", field: "Subject", label: "Request", preset: "desk.open", menu: "Review queue" }));
    await app.router.navigate({ to: "/desk/review/r1" });
    await waitFor(() => expect(observed).toMatchObject({ href: "/desk/review/r%2F2", collection: "/desk/review", title: "Reviews", field: "Question", label: "Review", menu: "Questions" }));
    await app.router.navigate({ to: "/teams/team-1" });
    expect(window.location.pathname).toBe("/teams/team-1");
    // No route is guarded: another root's pages open, and sit in the app's home root.
    await app.router.navigate({ to: "/teams/team-2" });
    expect(window.location.pathname).toBe("/teams/team-2");
    await waitFor(() => expect(observed).toMatchObject({ collection: "/desk/incoming", title: "Incoming", label: "Request" }));
    await app.router.navigate({ to: "/records/r1" });
    expect(window.location.pathname).toBe("/records/r1");
    await waitFor(() => expect(observed).toMatchObject({ href: "/desk/incoming/r%2F2", collection: "/desk/incoming", title: "Incoming" }));
  } finally { root.unmount(); host.remove(); }
});

test("a container condition naming an unknown route or app fails at boot; perspective is no condition", () => {
  const input = (when: Record<string, string>): CreateAppInput => testAppInput([{
    id: "desk",
    routes: [{ name: "desk.home", path: "/desk", component: EmptyPage }],
    menus: [{ id: "desk", route: "desk.home" }],
    containers: { "form#chrome": [
      { "desk.share": { content: createElement("span", null, "Share") } },
      { only: [], when },
    ] },
  }]);
  expect(() => createApp(input({ route: "desk.home" }))).not.toThrow();
  expect(() => createApp(input({ app: "desk", route: "desk.home" }))).not.toThrow();
  expect(() => createApp(input({ route: "desk.hmoe" }))).toThrow(/Addon "desk" narrows "form#chrome" on unknown route "desk.hmoe"/);
  expect(() => createApp(input({ app: "dsk" }))).toThrow(/narrows "form#chrome" in unknown app "dsk"/);
  expect(() => createApp(input({ perspective: "desk" }))).toThrow(/unknown condition "perspective"/);
});

test("a container's when.app matches every app on the page's trail, flattened ones too; a page is no app", async () => {
  const seen: { apps?: readonly string[] } = {};
  function Probe(): ReactNode {
    seen.apps = useAppRuntime().containerScope?.apps;
    return null;
  }
  const addons = (when: Record<string, string>) => [
    { id: "projects", routes: [{ name: "projects.tasks", path: "/projects/tasks", component: Probe }],
      menus: [{ id: "projects", children: [{ id: "projects.tasks", route: "projects.tasks" }] }] },
    { id: "pm", dependsOn: ["projects"], menus: { pm: { include: [{ id: "projects", flatten: true }] } },
      containers: { "form#chrome": [{ only: [], when }] } },
  ];
  expect(() => createApp(testAppInput(addons({ app: "projects" })))).not.toThrow();
  expect(() => createApp(testAppInput(addons({ app: "projects.tasks" })))).toThrow(/in unknown app "projects.tasks"/);
  const app = createApp(testAppInput(addons({ app: "pm" })));
  const host = document.createElement("div");
  document.body.append(host);
  const root = app.mount(host);
  try {
    await app.router.navigate({ to: "/projects/tasks" });
    await waitFor(() => expect(seen.apps).toEqual(["pm", "projects"]));
  } finally { root.unmount(); host.remove(); }
});

test("a mount serves the mounted page in the borrowing app, which keeps its links, words and anchor; matching records open there from every app", async () => {
  const seen: { route?: string | null; app?: string | null; menu?: string | null; apps?: readonly string[]; list?: string;
    record?: string; staff?: string; other?: string; label?: string } = {};
  function Probe(): ReactNode {
    const runtime = useAppRuntime();
    const href = useRouteHref();
    const lookup = useResourceRecordHrefLookup();
    Object.assign(seen, {
      route: runtime.activeRouteName, app: runtime.activeApp, menu: runtime.activeMenuId, apps: runtime.containerScope?.apps,
      list: href("iam.users"), record: href("iam.users.record", { id: "u1" }),
      staff: lookup("iam.User", "u2", { kind: "staff" }), other: lookup("iam.User", "u3", { kind: "guest" }),
      label: runtime.vocabulary.resources["iam.User"]?.label,
    });
    return null;
  }
  const addons: BaseAddon[] = [
    { id: "iam", routes: resourcePageRoutes("iam.users", "/iam/users", Probe, "iam.User", { menu: "iam.users" }),
      menus: { iam: {}, "iam.users": { parent: "iam", route: "iam.users" } } },
    { id: "desk", dependsOn: ["iam"],
      routes: [{ name: "desk.home", path: "/desk/home", component: EmptyPage }],
      menus: {
        desk: { route: "desk.home" },
        "desk.people": { parent: "desk", mount: "iam.users", path: "people", label: "People" },
        "desk.staff": { parent: "desk", mount: "iam.users", path: "staff", recordMatch: { field: "kind", equals: "staff" } },
      },
      vocabulary: [{ app: "desk", resources: { "iam.User": { label: "Colleague" } } }] },
  ];
  const input: CreateAppInput = {
    ...testAppInput(addons, { console: { requireAuth: false } }),
    schemas: testSchemasWithConsoleResources([
      testDataResource("iam.User", { query: testResourceQuery({ fields: { kind: testQueryField("kind") } }) }),
    ]),
  };
  // Only an addon depending on the route's owner may mount it.
  expect(() => createApp({ ...input, addons: [addons[0]!, { ...addons[1]!, dependsOn: [] }] }))
    .toThrow(/Addon "desk" mounts route "iam.users" of "iam", which it does not depend on/);
  history.replaceState(null, "", "/desk/people");
  const app = createApp(input);
  expect(routesByFullPath(app.router).has("/desk/people/$id")).toBe(true);
  const host = document.createElement("div");
  document.body.append(host);
  const root = app.mount(host);
  try {
    await waitFor(() => expect(seen).toMatchObject({
      route: "desk.people", app: "desk", menu: "desk.people", apps: ["desk"], label: "Colleague",
      list: "/desk/people", record: "/desk/people/u1", staff: "/desk/staff/u2", other: "/desk/people/u3",
    }));
    await app.router.navigate({ to: "/iam/users" });
    await waitFor(() => expect(seen).toMatchObject({
      route: "iam.users", app: "iam", menu: "iam.users", apps: ["iam"], label: undefined,
      list: "/iam/users", record: "/iam/users/u1", staff: "/desk/staff/u2", other: "/iam/users/u3",
    }));
  } finally { root.unmount(); host.remove(); }
});

test("a preset opening on a contributed view kind needs a resource#views child offering it", () => {
  const preset = { id: "desk.graph", label: "Graph", resource: "notes.Note", view: "nexus.graph" as const };
  const graph = { label: "Graph", icon: "network", capabilities: { grouping: false, pagination: false, columns: false, filter: true } };
  const input = (containers: BaseAddon["containers"]): CreateAppInput => ({
    ...testAppInput([{
      id: "desk", resourceViews: [preset],
      routes: resourcePageRoutes("desk.all", "/desk", EmptyPage, "notes.Note"),
      menus: [{ id: "desk", route: "desk.all" }],
    }, { id: "nexus", containers }]),
    schemas: testSchemasWithConsoleResources([testDataResource("notes.Note"), testDataResource("tasks.Task")]),
  });
  expect(() => createApp(input({ "notes.Note#views": { "nexus.graph": { content: graph } } }))).not.toThrow();
  expect(() => createApp(input({ "resource#views": { "nexus.graph": { content: graph } } }))).not.toThrow();
  expect(() => createApp(input({ "tasks.Task#views": { "nexus.graph": { content: graph } } })))
    .toThrow(/Resource view "desk.graph" opens on view kind "nexus.graph", which no addon contributes to "notes.Note#views"/);
});
