// @vitest-environment happy-dom
import { fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import {
  AppRuntimeProvider,
  useAppRuntime,
  useResourceRecordHref,
  useResourceRecordHrefLookup,
  useRouteHref,
  useRuntimeAuth,
  useRuntimeViewAs,
  useRuntimeUserPreferences,
  useNamespaceT,
  useT,
  useWidget,
  type AppRuntime,
  type RuntimeAuthState,
} from "./runtime";
import { containersFromChildren, useContainer, useDrawers, type ContainerScope } from "./containers";
import { createRouteHref } from "./route-href";
import { createAngeeI18nInstance } from "./i18n";
import { ViewAsBanner, ViewAsPicker } from "../chrome/ViewAs";

function wrapperFor(runtime: Partial<AppRuntime>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(ModelMetadataProvider, {
      metadata: TEST_METADATA,
      children: createElement(AppRuntimeProvider, { runtime, children }),
    });
}

const TEST_METADATA: SchemaFieldMetadata = schemaFieldMetadataFromDataResources([
    testDataResource("messaging.Thread"),
    testDataResource("messaging.Message"),
]);

test("preview UI consumes auth identity, focuses entry and restores the picker on exit", async () => {
  const realUser = { id: "manager-1", name: "Alex" };
  const currentUser = { id: "responder-1", name: "Morgan" };
  const exit = vi.fn();
  const viewAs = { viewAs: { userId: currentUser.id }, realUser, currentUser,
    viewablePeople: [currentUser], enter: vi.fn(), exit };
  const auth: RuntimeAuthState = { user: currentUser, status: "authenticated", hasRole: () => false, viewAs };
  const { result } = renderHook(() => useRuntimeViewAs(), { wrapper: wrapperFor({ auth }) });
  expect(result.current).toBe(viewAs);
  const children = <><ViewAsBanner /><ViewAsPicker /></>;
  const rendered = render(createElement(AppRuntimeProvider, { runtime: { auth }, children }));
  expect(screen.getByRole("status").textContent).toContain("Previewing as Morgan");
  expect(screen.getByRole("status").textContent).toContain("Signed in as Alex");
  expect(screen.getByRole("button", { name: "Preview as a person: Morgan" })).toBeTruthy();
  const exitButton = screen.getByRole("button", { name: "Exit preview" });
  expect(document.activeElement).toBe(exitButton);
  fireEvent.click(exitButton);
  expect(exit).toHaveBeenCalledOnce();
  rendered.rerender(createElement(AppRuntimeProvider, {
    runtime: { auth: { ...auth, user: realUser, viewAs: { ...viewAs, viewAs: null, currentUser: realUser } } },
    children,
  }));
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Preview as a person" })));
  expect(screen.queryByRole("status")).toBeNull();
  rendered.unmount();
});

describe("useWidget", () => {
  test("returns a registered widget by id", () => {
    const wrapper = wrapperFor({ widgets: { text: "TEXT_WIDGET" } });
    const { result } = renderHook(() => useWidget("text"), { wrapper });
    expect(result.current).toBe("TEXT_WIDGET");
  });

  test("returns undefined for an unknown widget", () => {
    const { result } = renderHook(() => useWidget("missing"));
    expect(result.current).toBeUndefined();
  });
});

describe("useResourceRecordHref", () => {
  test("selects a same-model destination from row facts and falls back to the canonical record", () => {
    const wrapper = wrapperFor({
      routesByResource: {
        "messaging.Thread": {
          collection: "messaging.threads",
          record: { name: "messaging.thread", param: "threadId" },
          recordFallback: { name: "messaging.thread", param: "threadId" },
          recordDestinations: [{ record: { name: "desk.thread", param: "id" }, match: { field: "queue.id", equals: "queue-a" } }],
        },
      },
      routeHref: createRouteHref([
        { name: "messaging.thread", path: "/messaging/threads/$threadId" },
        { name: "desk.thread", path: "/desk/threads/$id" },
      ]),
    });
    const { result } = renderHook(() => ({ href: useResourceRecordHref("messaging.Thread"), lookup: useResourceRecordHrefLookup() }), { wrapper });
    expect(result.current.href?.("thr 1", { queue: { id: "queue-a" } })).toBe("/desk/threads/thr%201");
    expect(result.current.lookup("messaging.Thread", "thr 1", { queue: { id: "queue-a" } })).toBe("/desk/threads/thr%201");
    expect(result.current.href?.("thr 1", { queue: { id: "other" } })).toBe("/messaging/threads/thr%201");
  });

  test("a destination claiming several values takes a record holding any of them", () => {
    const wrapper = wrapperFor({
      routesByResource: {
        "messaging.Thread": {
          collection: "messaging.threads",
          record: { name: "messaging.thread", param: "threadId" },
          recordFallback: { name: "messaging.thread", param: "threadId" },
          recordDestinations: [{ record: { name: "desk.thread", param: "id" }, match: { field: "kind", equals: ["bill", "refund"] } }],
        },
      },
      routeHref: createRouteHref([
        { name: "messaging.thread", path: "/messaging/threads/$threadId" },
        { name: "desk.thread", path: "/desk/threads/$id" },
      ]),
    });
    const { result } = renderHook(() => useResourceRecordHref("messaging.Thread"), { wrapper });
    expect(result.current?.("t1", { kind: "bill" })).toBe("/desk/threads/t1");
    expect(result.current?.("t1", { kind: "refund" })).toBe("/desk/threads/t1");
    expect(result.current?.("t1", { kind: "receipt" })).toBe("/messaging/threads/t1");
  });

  test("builds an encoded record href from the resource's composed route", () => {
    const wrapper = wrapperFor({
      routesByResource: {
        "messaging.Thread": {
          collection: "messaging.threads",
          record: { name: "messaging.thread", param: "threadId" },
        },
      },
      routeHref: createRouteHref([
        { name: "messaging.threads", path: "/messaging/threads" },
        { name: "messaging.thread", path: "/messaging/threads/$threadId" },
      ]),
    });
    const { result } = renderHook(() => useResourceRecordHref("messaging.Thread"), {
      wrapper,
    });

    expect(result.current?.("thr 1")).toBe("/messaging/threads/thr%201");
  });

  test("returns undefined when no route owns a known resource", () => {
    const wrapper = wrapperFor({});
    const { result } = renderHook(
      () => useResourceRecordHref("messaging.Message"),
      { wrapper },
    );

    expect(result.current).toBeUndefined();
  });

  test("degrades an unknown relation-follow resource to undefined with a development warning", () => {
    const wrapper = wrapperFor({});
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    const { result } = renderHook(
      () => useResourceRecordHref("missing.Resource"),
      { wrapper },
    );

    expect(result.current).toBeUndefined();
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/resource route lookup.*missing\.Resource/),
    );
    warn.mockRestore();
  });

  test("degrades a resource known only to another schema to undefined", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const wrapper = wrapperFor({
      routesByResource: {
        "integrate.OAuthClient": { collection: "integrate.oauth" },
      },
    });

    const { result } = renderHook(
      () => useResourceRecordHref("integrate.OAuthClient"),
      { wrapper },
    );

    expect(result.current).toBeUndefined();
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/Unknown model spelling "integrate\.OAuthClient"/),
    );
    warn.mockRestore();
  });

  test("dynamically resolves composed record routes and degrades absent resources", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const wrapper = wrapperFor({
      routesByResource: {
        "messaging.Thread": {
          collection: "messaging.threads",
          record: { name: "messaging.thread", param: "threadId" },
        },
      },
      routeHref: createRouteHref([
        { name: "messaging.threads", path: "/messaging/threads" },
        { name: "messaging.thread", path: "/messaging/threads/$threadId" },
      ]),
    });
    const { result } = renderHook(() => useResourceRecordHrefLookup(), { wrapper });

    expect(result.current("messaging.Thread", "thr 1")).toBe(
      "/messaging/threads/thr%201",
    );
    expect(result.current("messaging.Message", "msg-1")).toBeUndefined();
    expect(result.current("missing.Model", "record-1")).toBeUndefined();
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/resource record route lookup.*missing\.Model/),
    );
    warn.mockRestore();
  });
});

describe("useRouteHref", () => {
  test("returns the app-composed route href owner", () => {
    const descriptors = [{ name: "notes.record", path: "/notes/$id" }];
    const routeHref = createRouteHref(descriptors);
    const wrapper = wrapperFor({ routeHref });
    const { result } = renderHook(() => useRouteHref(), { wrapper });

    expect(result.current("notes.record", { id: "note 1" })).toBe(
      "/notes/note%201",
    );
  });

  test("throws the named context error without a provider", () => {
    expect(() => renderHook(() => useRouteHref())).toThrow(
      /AppRuntime is unavailable: render within its <AppRuntime> provider/,
    );
  });

  test("the optional empty runtime probe degrades", () => {
    const { result } = renderHook(() =>
      useAppRuntime().routeHref.maybe("missing.route")
    );
    expect(result.current).toBeUndefined();
  });
});

describe("AppRuntimeProvider", () => {
  test("nested providers overlay dynamic session state without dropping registries", () => {
    function wrapper({ children }: { children: ReactNode }) {
      return (
        <AppRuntimeProvider runtime={{ widgets: { text: "TEXT_WIDGET" } }}>
          <AppRuntimeProvider
            runtime={{
              auth: {
                user: { id: "user_1", name: "Ada Lovelace" },
                status: "authenticated",
                hasRole: () => false,
              },
            }}
          >
            {children}
          </AppRuntimeProvider>
        </AppRuntimeProvider>
      );
    }
    const { result } = renderHook(
      () => ({
        widget: useWidget("text"),
        auth: useRuntimeAuth(),
      }),
      { wrapper },
    );

    expect(result.current.widget).toBe("TEXT_WIDGET");
    expect(result.current.auth.user?.name).toBe("Ada Lovelace");
  });
});

describe("useContainer", () => {
  const containers = containersFromChildren(
    [{ address: "form#actions", models: true }, { address: "shell#notices" }],
    {
      "form#actions": { "base.share": { content: "share", sequence: 50 } },
      "messaging.Thread#actions": {
        "messaging.resume": { content: "resume", sequence: 10 },
        "matrix.resume": { content: "matrix resume", sequence: 10, variant: { of: "messaging.resume", impl: "matrix" } },
      },
      "messaging.Message#actions": { "messaging.reply": { content: "reply" } },
      "shell#notices": { "operator.banner": { content: "banner" } },
    },
  );

  test("merges the kind address with the record's model addresses in position order", () => {
    const models = ["messaging.Thread"];
    const { result } = renderHook(() => useContainer("form#actions", { models }), { wrapper: wrapperFor({ containers }) });
    expect(result.current.map((child) => child.id)).toEqual(["messaging.resume", "base.share"]);
  });

  test("a variant replaces its original on rows of its implementation", () => {
    const models = ["messaging.Thread"];
    const impls = ["matrix"];
    const { result } = renderHook(() => useContainer("form#actions", { models, impls }), { wrapper: wrapperFor({ containers }) });
    expect(result.current.map((child) => child.content)).toEqual(["matrix resume", "share"]);
  });

  test("applies a layer's narrowing only where the runtime scope matches its condition", () => {
    const narrowed = { ...containers, rules: { "shell#notices": [{ layer: "pm", rank: 1, when: { route: "pm.board" }, only: [], exempt: [] }] } };
    const scoped = (routes: string[]): ContainerScope => ({ apps: [], routes });
    const onBoard = renderHook(() => useContainer("shell#notices"), { wrapper: wrapperFor({ containers: narrowed, containerScope: scoped(["pm.board.card", "pm.board"]) }) });
    expect(onBoard.result.current).toEqual([]);
    const elsewhere = renderHook(() => useContainer("shell#notices"), { wrapper: wrapperFor({ containers: narrowed, containerScope: scoped(["pm.list"]) }) });
    expect(elsewhere.result.current.map((child) => child.id)).toEqual(["operator.banner"]);
  });

  test("an undeclared container or a runtime without containers resolves empty", () => {
    expect(renderHook(() => useContainer("nobody#toolbar"), { wrapper: wrapperFor({ containers }) }).result.current).toEqual([]);
    expect(renderHook(() => useContainer("form#actions")).result.current).toEqual([]);
  });
});

describe("useRuntimeUserPreferences", () => {
  test("defaults to empty preferences outside the app auth provider", () => {
    const { result } = renderHook(() => useRuntimeUserPreferences());

    expect(result.current.available).toBe(false);
    expect(result.current.preferences).toEqual({});
  });
});

describe("useDrawers", () => {
  const containers = containersFromChildren(
    [{ address: "shell#drawers-right" }, { address: "shell#drawers-bottom" }],
    {
      "shell#drawers-bottom": {
        "operator.logs": { content: { title: "Logs", icon: "terminal", render: () => null }, sequence: 20 },
        "operator.tail": { content: { title: "Tail", render: () => null }, sequence: 10 },
      },
      "shell#drawers-right": { "agents.chat": { content: { title: "Chat", render: () => null } } },
    },
  );

  test("returns the edge's drawers in composed order, tagged with their edge", () => {
    const { result } = renderHook(() => useDrawers("bottom"), { wrapper: wrapperFor({ containers }) });
    expect(result.current.map(({ id, edge, title, sequence, icon }) => ({ id, edge, title, sequence, icon }))).toEqual([
      { id: "operator.tail", edge: "bottom", title: "Tail", sequence: 10, icon: undefined },
      { id: "operator.logs", edge: "bottom", title: "Logs", sequence: 20, icon: "terminal" },
    ]);
  });

  test("returns only the drawers contributed to the requested edge", () => {
    const { result } = renderHook(() => useDrawers("right"), { wrapper: wrapperFor({ containers }) });
    expect(result.current.map((drawer) => drawer.id)).toEqual(["agents.chat"]);
  });

  test("is empty when nothing is contributed", () => {
    const { result } = renderHook(() => useDrawers("right"));
    expect(result.current).toEqual([]);
  });
});

describe("useT", () => {
  test("resolves a key in its namespace and interpolates vars", () => {
    const wrapper = wrapperFor({
      i18n: createAngeeI18nInstance({ notes: { greet: "Hi {name}" } }),
    });
    const { result } = renderHook(() => useT("notes"), { wrapper });
    expect(result.current("greet", { name: "Ada" })).toBe("Hi Ada");
  });

  test("falls back to the key when the namespace lacks it", () => {
    const { result } = renderHook(() => useT("notes"));
    expect(result.current("missing")).toBe("missing");
  });
});

describe("useNamespaceT", () => {
  test("resolves English one/other bundles without an i18n provider", () => {
    const fallback = {
      item_one: "{count} item",
      item_other: "{count} items",
    };
    const { result } = renderHook(() => useNamespaceT("fixture", fallback));

    expect(result.current("item", { count: 1 })).toBe("1 item");
    expect(result.current("item", { count: 2 })).toBe("2 items");
    expect(result.current("item", { count: 0 })).toBe("0 items");
  });

  test("uses native plural defaults for missing host keys and preserves host translations", () => {
    const fallback = {
      item_zero: "No items for {name}",
      item_one: "{count} item for {name}",
      item_other: "{count} items for {name}",
      greeting: "Hello {name}",
    };
    const i18n = createAngeeI18nInstance({ fixture: { greeting: "Welcome {name}" } });
    const { result } = renderHook(() => useNamespaceT("fixture", fallback), {
      wrapper: wrapperFor({ i18n }),
    });

    expect(result.current("item", { count: 0, name: "Ada" })).toBe("No items for Ada");
    expect(result.current("item", { count: 1, name: "Ada" })).toBe("1 item for Ada");
    expect(result.current("item", { count: 2, name: "Ada" })).toBe("2 items for Ada");
    expect(result.current("greeting", { name: "Ada" })).toBe("Welcome Ada");
    expect(result.current("missing")).toBe("missing");
  });

  test("uses the active locale's plural suffix for bundled defaults", () => {
    const fallback = { item_one: "{count} item", item_few: "{count} few", item_other: "{count} items" };
    const i18n = createAngeeI18nInstance({}, "cs");
    const { result } = renderHook(() => useNamespaceT("fixture", fallback), {
      wrapper: wrapperFor({ i18n }),
    });

    expect(result.current("item", { count: 3 })).toBe("3 few");
  });

  test("lets the locale choose zero's category unless the bundle declares zero", () => {
    const fallback = { item_one: "{count} item", item_other: "{count} items" };
    const i18n = createAngeeI18nInstance({}, "fr");
    const { result } = renderHook(() => useNamespaceT("fixture", fallback), {
      wrapper: wrapperFor({ i18n }),
    });

    expect(result.current("item", { count: 0 })).toBe("0 item");
    expect(result.current("item", { count: 2 })).toBe("2 items");
  });

  test("uses the other default when a locale category is absent from the bundle", () => {
    const fallback = { item_one: "{count} item", item_other: "{count} items" };
    const i18n = createAngeeI18nInstance({}, "cs");
    const { result } = renderHook(() => useNamespaceT("fixture", fallback), {
      wrapper: wrapperFor({ i18n }),
    });

    expect(result.current("item", { count: 3 })).toBe("3 items");
  });
});
