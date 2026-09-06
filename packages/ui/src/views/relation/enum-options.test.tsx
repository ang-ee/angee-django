// @vitest-environment happy-dom

import type { PropsWithChildren, ReactElement } from "react";
import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { useImplConfigFields } from "./enum-options";

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: () => ({
      data: {
        impl_choices: [
          {
            key: "local",
            category: "Local",
            defaults: {},
            config_schema: {
              type: "object",
              properties: {
                local_root: { type: "string", label: "Local root" },
                local_name: { type: "string", label: "Local name" },
              },
              required: ["local_root"],
            },
          },
          { key: "legacy", category: "Legacy", defaults: {}, config_schema: null },
          {
            key: "first-conflict",
            category: "Conflict",
            defaults: {},
            config_schema: {
              type: "object",
              properties: {
                shared: { type: "string", label: "Shared string", defaultValue: "first" },
                first_only: { type: "string", label: "First only" },
              },
            },
          },
          {
            key: "second-conflict",
            category: "Conflict",
            defaults: {},
            config_schema: {
              type: "object",
              properties: {
                shared: { type: "boolean", label: "Shared boolean", defaultValue: true },
                second_only: { type: "string", label: "Second only" },
              },
            },
          },
        ],
      },
    }),
  };
});

describe("useImplConfigFields", () => {
  test("projects declared config through FormSpec and leaves undeclared choices raw", () => {
    const { result } = renderHook(
      () => useImplConfigFields("integrate.VcsBridge", "backend_class"),
      { wrapper: RuntimeOwner },
    );

    expect(result.current.fields.slice(0, 2).map(({ name, label, required }) => ({ name, label, required })))
      .toEqual([
        { name: "config.local_root", label: "Local root", required: true },
        { name: "config.local_name", label: "Local name", required: undefined },
      ]);
    expect(result.current.fields[0]?.showWhen?.({ backend_class: "local" })).toBe(true);
    expect(result.current.fields[0]?.showWhen?.({ backend_class: "legacy" })).toBe(false);
    expect(result.current.hasSchema("local")).toBe(true);
    expect(result.current.hasSchema("legacy")).toBe(false);
    expect(result.current.hasSchema("missing")).toBe(false);
    expect(result.current.hasSchema("first-conflict")).toBe(true);
    expect(result.current.hasSchema("second-conflict")).toBe(true);
    expect(result.current.fields.map(({ name }) => name)).toContain("config.first_only");
    expect(result.current.fields.map(({ name }) => name)).toContain("config.second_only");
    const shared = result.current.fields.find(({ name }) => name === "config.shared");
    expect(shared?.resolve?.({ backend_class: "first-conflict" })).toMatchObject({
      name: "config.shared", label: "Shared string", widget: "text", defaultValue: "first",
    });
    expect(shared?.resolve?.({ backend_class: "second-conflict" })).toMatchObject({
      name: "config.shared", label: "Shared boolean", widget: "boolean", defaultValue: true,
    });
  });
});

function RuntimeOwner({ children }: PropsWithChildren): ReactElement {
  return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>{children}</AppRuntimeProvider>;
}
