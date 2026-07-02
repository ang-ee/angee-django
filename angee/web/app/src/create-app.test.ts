// @vitest-environment happy-dom

import { createElement, type ReactNode } from "react";
import { cleanup, waitFor } from "@testing-library/react";
import { createAngeeHasuraDataProvider } from "@angee/refine";
import { useAuthoredQuery } from "@angee/refine";
import { useChatterRoutes, useResourceRoute } from "@angee/ui/runtime";
import { useParams } from "@tanstack/react-router";
import { afterEach, describe, expect, test } from "vitest";

import {
  createApp,
  parseFlatSearch,
  stringifyFlatSearch,
  type BaseAddon,
  type CreateAppInput,
  type RefineLayoutChromeProps,
} from "./create-app";
import { MenuTree, type ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import {
  captureChrome,
  chromeSnapshot,
  testGraphQLFetch,
  TEST_SCHEMAS,
} from "./testing";
import {
  resourceViewSearchToState,
  resourceViewStateToSearch,
  mergeResourceViewSearch,
} from "@angee/ui/views/resource-view-model";
import type { DataResourceMetadata } from "@angee/metadata";

afterEach(() => cleanup());

type AuthoredQueryDocument = Parameters<typeof useAuthoredQuery>[0];

function typedDocument(source: string): AuthoredQueryDocument {
  return source as unknown as AuthoredQueryDocument;
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
    });
    expect(query).not.toContain("%22board%22");
  });

  test("preserves foreign search keys when resource-view state changes", () => {
    const current = parseFlatSearch(
      "?tab=archive&page=2&view=board&group=status:year",
    );
    const currentState = resourceViewSearchToState(current);
    const nextState = currentState.reduce({
      type: "setSort",
      sort: { field: "title", dir: "asc" },
    });

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
      home: "/public-page",
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
      home: "/operator-page",
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
              path: "/login",
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
      home: "/private",
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
        expect(window.location.pathname).toBe("/login");
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

  test("marks authored menu roots so repeated crumbs do not become rail apps", async () => {
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
      expect(tree.byId.get("agents")?.appRoot).toBe(true);
      expect(tree.byId.get("agents.home")?.appRoot).toBeUndefined();
      expect(tree.trailFor("agents.home").map((item) => item.id)).toEqual([
        "agents",
        "agents.group",
        "agents.home",
      ]);
      expect(tree.appSectionItems("/agents").map((item) => item.id)).toEqual([
        "agents.group",
      ]);
    } finally {
      captured.cleanup();
    }
  });

  test("preserves same-label grouped app menus for top-nav rendering", async () => {
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
      expect(tree.appSectionItems("/agents/skills").map((item) => item.id)).toEqual([
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

      expect(tree.appSectionItems("/agents").map((item) => item.id)).toEqual([
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
      createApp(testAppInput([
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
      ])),
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

  test("rejects a route that references an unknown menu item", () => {
    expect(() =>
      createApp(testAppInput([
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
      ])),
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
      return createElement("span", null, `route ${path ?? "none"}`);
    }

    const app = createApp(testAppInput([
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
    ]));
    const root = app.mount(host);

    try {
      await waitFor(() => {
        expect(host.textContent).toContain("route /clients");
      });
    } finally {
      root.unmount();
      host.remove();
    }
  });

  test("rejects two routes claiming the same resource", () => {
    expect(() =>
      createApp(testAppInput([
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
              resource: "OAuthClient",
            },
          ],
        },
      ])),
    ).toThrow(/claims resource "OAuthClient"/);
  });

  test("exposes inherited model labels on chatter record routes", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/notes/abc");

    function ChatterRouteProbe(): ReactNode {
      const route = useChatterRoutes().find((item) => item.name === "notes.record");
      return createElement(
        "span",
        null,
        `${route?.modelLabel ?? "none"} ${route?.recordParam ?? "none"}`,
      );
    }

    const app = createApp(testAppInput([
      {
        id: "notes",
        routes: [
          {
            name: "notes.home",
            path: "/notes",
            layout: "console",
            component: ChatterRouteProbe,
            resource: "notes.Note",
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
        expect(host.textContent).toContain("notes.Note id");
      });
    } finally {
      root.unmount();
      host.remove();
    }
  });
});

describe("createApp route tree", () => {
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

function testDataResource(modelLabel: string): DataResourceMetadata {
  const modelName = modelLabel.split(".").at(-1) ?? modelLabel;
  const list = `${modelName.toLowerCase()}s`;
  return {
    schemaName: "console",
    modelLabel,
    appLabel: modelLabel.includes(".") ? modelLabel.split(".")[0] ?? "" : "",
    modelName,
    publicIdField: "id",
    roots: {
      list,
      detail: `${list}_by_pk`,
      create: `insert_${list}_one`,
      update: `update_${list}_by_pk`,
      delete: `delete_${list}_by_pk`,
    },
    typeNames: { node: `${modelName}Type` },
    capabilities: ["list", "detail", "create", "update", "delete"],
    fields: [],
    filterFields: [],
    orderFields: [],
    aggregateFields: [],
    groupByFields: [],
    relationAxes: [],
  };
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
