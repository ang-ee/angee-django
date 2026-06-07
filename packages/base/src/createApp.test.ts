// @vitest-environment happy-dom

import { createElement, type ReactNode } from "react";
import { cleanup, waitFor } from "@testing-library/react";
import {
  useAuthoredQuery,
} from "@angee/sdk";
import { useParams } from "@tanstack/react-router";
import { afterEach, describe, expect, test } from "vitest";

import {
  createApp,
  parseFlatSearch,
  stringifyFlatSearch,
  type BaseAddon,
  type ShellChromeProps,
} from "./createApp";
import type { ChromeMenuItem } from "./chrome/menu-tree";
import {
  captureChrome,
  chromeSnapshot,
  TEST_SCHEMAS,
} from "./testing";
import {
  dataViewSearchToState,
  dataViewStateToSearch,
  mergeDataViewSearch,
} from "./views/data-view-model";

afterEach(() => cleanup());

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

  test("keeps primitive data-view search values unquoted", () => {
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

  test("preserves foreign search keys when data-view state changes", () => {
    const current = parseFlatSearch(
      "?tab=archive&page=2&view=board&group=status:year",
    );
    const currentState = dataViewSearchToState(current);
    const nextState = currentState.reduce({
      type: "setSort",
      sort: { field: "title", dir: "asc" },
    });

    const query = stringifyFlatSearch(
      mergeDataViewSearch(current, dataViewStateToSearch(nextState)),
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
  test("pins public shell routes and lets console routes inherit the default schema", async () => {
    const seen: Record<string, string> = {};
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/public-page");

    function PublicPage(): ReactNode {
      useAuthoredQuery("query PublicProbe { schemaProbe }");
      return createElement("span", null, "Public probe");
    }

    function ConsolePage(): ReactNode {
      useAuthoredQuery("query ConsoleProbe { schemaProbe }");
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
              shell: "public",
              component: PublicPage,
            },
            {
              name: "console.page",
              path: "/console-page",
              shell: "console",
              component: ConsolePage,
            },
          ],
        },
      ],
      defaultSchema: "console",
      subscriptionSchema: "console",
      home: "/public-page",
      shells: {
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

describe("createApp route menu refs", () => {
  test("resolves menu route refs and derives route chrome from the menu trail", async () => {
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
              shell: "console",
              component: EmptyPage,
            },
            {
              name: "admin.users",
              path: "/admin/users",
              shell: "console",
              component: EmptyPage,
            },
          ],
          menus,
        },
      ],
    });

    try {
      expect(chromeSnapshot(captured.props())).toEqual({
        title: "Admin",
        icon: "auth",
        breadcrumbs: [
          { label: "Admin", to: "/admin" },
          { label: "Users" },
        ],
      });
      expect(
        captured.props().menus[0]?.children?.[0]?.to,
      ).toBe("/admin/users");
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
              shell: "console",
              component: EmptyPage,
            },
          ],
          menus: [{ id: "bad", route: "bad.home", to: "/bad" }],
        },
      ])),
    ).toThrow(/declares both route and to/);
  });

  test("rejects ambiguous route chrome when multiple menu items reference one route", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "ambiguous",
          routes: [
            {
              name: "shared.home",
              path: "/shared",
              shell: "console",
              component: EmptyPage,
            },
          ],
          menus: [
            { id: "shared.a", label: "Shared A", route: "shared.home" },
            { id: "shared.b", label: "Shared B", route: "shared.home" },
          ],
        },
      ])),
    ).toThrow(/referenced by multiple menu items/);
  });

  test("allows multiple menu refs when every chrome field is explicit", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "explicit",
          routes: [
            {
              name: "explicit.home",
              path: "/explicit",
              shell: "console",
              title: "Explicit",
              icon: "auth",
              breadcrumbs: [{ label: "Explicit" }],
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

  test("reports only the explicit chrome fields that would need derivation", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "partial",
          routes: [
            {
              name: "partial.home",
              path: "/partial",
              shell: "console",
              title: "Partial",
              component: EmptyPage,
            },
          ],
          menus: [
            { id: "partial.a", label: "Partial A", route: "partial.home" },
            { id: "partial.b", label: "Partial B", route: "partial.home" },
          ],
        },
      ])),
    ).toThrow(/explicit chrome for icon, breadcrumbs/);
  });

  test("requires route.menu to select one of the route's menu refs when refs exist", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "wrong-menu",
          routes: [
            {
              name: "wrong.home",
              path: "/wrong",
              shell: "console",
              menu: "wrong.other",
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
              shell: "console",
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
              shell: "console",
              menu: "missing-menu",
              component: EmptyPage,
            },
          ],
        },
      ])),
    ).toThrow(/references unknown menu item "missing-menu"/);
  });
});

describe("createApp route tree", () => {
  test("nests addon routes under shell layouts and declared parents", () => {
    const app = createApp(testAppInput([
      {
        id: "notes",
        routes: [
          {
            name: "notes.home",
            path: "/notes",
            shell: "console",
            component: EmptyPage,
          },
          {
            name: "notes.record",
            path: "/notes/$id",
            shell: "console",
            parent: "notes.home",
          },
        ],
      },
    ]));
    const routes = routesByFullPath(app.router);
    const shell = shellRoute(app.router, "console");
    const home = routes.get("/notes");
    const record = routes.get("/notes/$id");

    expect(shell).toBeTruthy();
    expect(home?.parentRoute).toBe(shell);
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
            shell: "console",
            component: NotePageProbe,
          },
          {
            name: "notes.record",
            path: "/notes/$id",
            shell: "console",
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
              shell: "console",
              parent: "missing",
            },
          ],
        },
      ])),
    ).toThrow(/references unknown parent route "missing"/);
  });

  test("rejects a parent route from another shell", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "cross-shell",
          routes: [
            {
              name: "public.parent",
              path: "/public",
              shell: "public",
              component: EmptyPage,
            },
            {
              name: "console.child",
              path: "/public/child",
              shell: "console",
              parent: "public.parent",
            },
          ],
        },
      ], {
        console: { chrome: TestChrome, requireAuth: false },
        public: { chrome: TestChrome, requireAuth: false },
      })),
    ).toThrow(/must use the same shell/);
  });

  test("rejects a parent route whose path is not a proper prefix", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "bad-prefix",
          routes: [
            {
              name: "notes.home",
              path: "/notes",
              shell: "console",
              component: EmptyPage,
            },
            {
              name: "notes.record",
              path: "/not-notes/$id",
              shell: "console",
              parent: "notes.home",
            },
          ],
        },
      ])),
    ).toThrow(/must be nested under parent/);
  });

  test("rejects a non-nested route without a component", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "missing-component",
          routes: [
            {
              name: "empty.home",
              path: "/empty",
              shell: "console",
            },
          ],
        },
      ])),
    ).toThrow(/must declare component/);
  });

  test("rejects a route that references an undeclared shell", () => {
    expect(() =>
      createApp(testAppInput([
        {
          id: "bad-shell",
          routes: [
            {
              name: "bad.home",
              path: "/bad",
              shell: "missing",
              component: EmptyPage,
            },
          ],
        },
      ])),
    ).toThrow(/references undeclared shell "missing"/);
  });
});

function TestChrome({ children }: ShellChromeProps): ReactNode {
  return children;
}

function EmptyPage(): ReactNode {
  return null;
}

function testAppInput(
  addons: readonly BaseAddon[],
  shells: Parameters<typeof createApp>[0]["shells"] = {
    console: { chrome: TestChrome, requireAuth: false },
  },
): Parameters<typeof createApp>[0] {
  return {
    addons,
    shells,
    schemas: TEST_SCHEMAS,
    defaultSchema: "console",
    subscriptionSchema: "console",
  };
}

function routesByFullPath(router: unknown): Map<string, TestRoute> {
  const routes = Object.values((router as TestRouter).routesById);
  return new Map(routes.map((route) => [route.fullPath, route]));
}

function shellRoute(router: unknown, shell: string): TestRoute | undefined {
  return Object.values((router as TestRouter).routesById).find((route) =>
    route.id.endsWith(`_angee_shell_${shell}`),
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
        data: { __typename: "Query", currentUser: null, schemaProbe: schema },
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
