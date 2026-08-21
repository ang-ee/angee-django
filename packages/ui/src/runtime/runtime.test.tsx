// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import {
  ModelMetadataProvider,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import {
  AppRuntimeProvider,
  useAppRuntime,
  useDrawers,
  useModelSlot,
  useResourceRecordHref,
  useResourceRecordHrefLookup,
  useRouteHref,
  useRuntimeAuth,
  useRuntimeUserPreferences,
  useSlot,
  useT,
  useWidget,
  type AppRuntime,
  type RuntimeI18n,
} from "./runtime";
import { createRouteHref } from "./route-href";

function wrapperFor(runtime: Partial<AppRuntime>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(ModelMetadataProvider, {
      metadata: TEST_METADATA,
      children: createElement(AppRuntimeProvider, { runtime, children }),
    });
}

const TEST_METADATA: SchemaFieldMetadata = {
  types: {},
  resources: [
    testDataResource("messaging.Thread"),
    testDataResource("messaging.Message"),
  ],
};

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

describe("useSlot", () => {
  test("returns only the entries contributed to the requested slot", () => {
    const wrapper = wrapperFor({
      slots: [
        { slot: "header", id: "a" },
        { slot: "footer", id: "b" },
        { slot: "header", id: "c" },
      ],
    });
    const { result } = renderHook(() => useSlot("header"), { wrapper });
    expect(result.current.map((entry) => entry.id)).toEqual(["a", "c"]);
  });
});

describe("useModelSlot", () => {
  test("matches slot, model, and impl in target order", () => {
    const wrapper = wrapperFor({
      slots: [
        { slot: "form-view.record-actions", model: "messaging.Thread", id: "base" },
        {
          slot: "form-view.record-actions",
          model: "messaging.Thread",
          impl: "matrix",
          id: "specialized",
        },
        { slot: "form-view.record-actions", model: "messaging.Message", id: "other" },
      ],
    });
    const targets = [
      { slot: "form-view.record-actions", model: "messaging.Thread" },
      { slot: "form-view.record-actions", model: "messaging.Thread", impl: "matrix" },
    ];
    const { result } = renderHook(() => useModelSlot(targets), { wrapper });

    expect(result.current.map((entry) => entry.id)).toEqual([
      "base",
      "specialized",
    ]);
  });
});

describe("useRuntimeUserPreferences", () => {
  test("defaults to empty preferences outside the app auth provider", () => {
    const { result } = renderHook(() => useRuntimeUserPreferences());

    expect(result.current.preferences).toEqual({});
  });
});

describe("useDrawers", () => {
  const drawers = [
    { id: "logs", edge: "bottom" as const, title: "Logs", render: () => null },
    { id: "chat", edge: "right" as const, title: "Chat", render: () => null },
    { id: "tail", edge: "bottom" as const, title: "Tail", render: () => null },
  ];

  test("returns every drawer when no edge is given", () => {
    const wrapper = wrapperFor({ drawers });
    const { result } = renderHook(() => useDrawers(), { wrapper });
    expect(result.current.map((d) => d.id)).toEqual(["logs", "chat", "tail"]);
  });

  test("returns only the drawers contributed to the requested edge", () => {
    const wrapper = wrapperFor({ drawers });
    const { result } = renderHook(() => useDrawers("bottom"), { wrapper });
    expect(result.current.map((d) => d.id)).toEqual(["logs", "tail"]);
  });

  test("is empty when nothing is contributed", () => {
    const { result } = renderHook(() => useDrawers("right"));
    expect(result.current).toEqual([]);
  });
});

describe("useT", () => {
  test("resolves a key in its namespace and interpolates vars", () => {
    const wrapper = wrapperFor({
      i18n: testI18n({ notes: { greet: "Hi {name}" } }),
    });
    const { result } = renderHook(() => useT("notes"), { wrapper });
    expect(result.current("greet", { name: "Ada" })).toBe("Hi Ada");
  });

  test("falls back to the key when the namespace lacks it", () => {
    const { result } = renderHook(() => useT("notes"));
    expect(result.current("missing")).toBe("missing");
  });
});

function testI18n(
  resources: Record<string, Record<string, string>>,
): RuntimeI18n {
  return {
    getFixedT: (_lng, namespace) => (key, options = {}) => {
      const template = resources[namespace]?.[key] ?? options.defaultValue ?? key;
      return template.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name: string) => {
        const value = options[name];
        return value === undefined ? match : String(value);
      });
    },
  };
}
