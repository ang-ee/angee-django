// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { useActionResultRun } from "./action-result-run";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: {
    success: vi.fn(),
    danger: vi.fn(),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock("../../feedback", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useToast: () => mocks.toast,
}));

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <ModelMetadataProvider
      metadata={schemaFieldMetadataFromDataResources([
          testDataResource("example.Item"),
          testDataResource("example.Document"),
      ])}
    >
      <AppRuntimeProvider
        runtime={{
          routesByResource: {
            "example.Item": {
              collection: "example.items",
              record: { name: "example.item", param: "id" },
            },
          },
          routeHref: createRouteHref([
            { name: "example.items", path: "/example/items" },
            { name: "example.item", path: "/example/items/$id" },
          ]),
        }}
      >
        {children}
      </AppRuntimeProvider>
    </ModelMetadataProvider>
  );
}

beforeEach(() => {
  mocks.navigate.mockClear();
  mocks.toast.success.mockClear();
  mocks.toast.danger.mockClear();
});

describe("useActionResultRun", () => {
  test("toasts success and deep-links to the created record", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "example.Item" }),
      { wrapper },
    );

    await act(async () => {
      const outcome = await result.current(async () => ({
        ok: true,
        message: "Item created.",
        id: "item_9",
      }));
      expect(outcome).toEqual({
        ok: true,
        message: "Item created.",
        id: "item_9",
      });
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({
      title: "Item created.",
    });
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/example/items/item_9" });
    expect(mocks.toast.danger).not.toHaveBeenCalled();
  });

  test("an id-less success (an exhausted idempotent verb) only toasts", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "example.Item" }),
      { wrapper },
    );

    await act(async () => {
      await result.current(async () => ({ ok: true, message: "Nothing to create." }));
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Nothing to create." });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("a resource without a routed page never navigates", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "example.Document" }),
      { wrapper },
    );

    await act(async () => {
      await result.current(async () => ({ ok: true, message: "Recorded.", id: "doc_1" }));
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Recorded." });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("metadata-absent linkTo only toasts and warns instead of throwing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const noMetadataWrapper = ({ children }: { children: React.ReactNode }) => (
      <AppRuntimeProvider
        runtime={{
          routesByResource: {
            "example.Item": {
              collection: "example.items",
              record: { name: "example.item", param: "id" },
            },
          },
          routeHref: createRouteHref([
            { name: "example.items", path: "/example/items" },
            { name: "example.item", path: "/example/items/$id" },
          ]),
        }}
      >
        {children}
      </AppRuntimeProvider>
    );
    const { result } = renderHook(
      () => useActionResultRun({ linkTo: "example.Item" }),
      { wrapper: noMetadataWrapper },
    );

    await act(async () => {
      await result.current(async () => ({ ok: true, message: "Created.", id: "item_9" }));
    });

    expect(mocks.toast.success).toHaveBeenCalledWith({ title: "Created." });
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/resource route lookup.*exposes no resources/),
    );
    warn.mockRestore();
  });

  test("a domain failure toasts danger with its in-band non-field reasons", async () => {
    const { result } = renderHook(() => useActionResultRun(), { wrapper });

    await act(async () => {
      const outcome = await result.current(async () => ({
        ok: false,
        message: "Confirm failed.",
        validationErrors: {
          __all__: ["You are not allowed to modify this record."],
        },
      }));
      expect(outcome?.ok).toBe(false);
    });

    expect(mocks.toast.danger).toHaveBeenCalledWith({
      title: "Confirm failed.",
      description: "You are not allowed to modify this record.",
    });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test("a missing payload toasts the no-result title", async () => {
    const { result } = renderHook(() => useActionResultRun(), { wrapper });

    await act(async () => {
      const outcome = await result.current(async () => undefined);
      expect(outcome).toBeUndefined();
    });

    expect(mocks.toast.danger).toHaveBeenCalledWith({
      title: "The action returned no result.",
    });
  });

  test("a thrown transport error settles into a danger toast", async () => {
    const { result } = renderHook(
      () => useActionResultRun({ noResultTitle: "Verb failed." }),
      { wrapper },
    );

    await act(async () => {
      const outcome = await result.current(async () => {
        throw new Error("socket closed");
      });
      expect(outcome).toBeUndefined();
    });

    expect(mocks.toast.danger).toHaveBeenCalledWith({ title: "socket closed" });
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
});
