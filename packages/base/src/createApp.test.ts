// @vitest-environment happy-dom

import { createElement, type ReactNode } from "react";
import { cleanup, waitFor } from "@testing-library/react";
import { useAuthoredQuery } from "@angee/sdk";
import { afterEach, describe, expect, test } from "vitest";

import {
  createApp,
  parseFlatSearch,
  stringifyFlatSearch,
  type ShellChromeProps,
} from "./createApp";
import {
  dataViewReducer,
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
    const nextState = dataViewReducer(currentState, {
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

function TestChrome({ children }: ShellChromeProps): ReactNode {
  return children;
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
