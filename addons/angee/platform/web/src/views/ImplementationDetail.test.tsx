// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";

const mocks = vi.hoisted(() => ({
  query: {} as Record<string, unknown>,
  routeHref: vi.fn((name: string, params?: Record<string, string>) =>
    `/${name}/${params?.id ?? ""}`),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => mocks.query,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useRouteHref: () => mocks.routeHref,
  useRouteRecordId: () => "workflows.Step.impl:decision",
}));

vi.mock("../i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../i18n")>()),
  usePlatformT: () => (key: string) => key,
}));

vi.mock("../lib/cells", () => ({
  TextRouteLink: ({ href, children }: { href: string; children: React.ReactNode }) =>
    <a href={href}>{children}</a>,
}));

import platform from "../index";
import { ImplementationDetail } from "./ImplementationDetail";

const implementation = {
  id: "workflows.Step.impl:decision",
  model: "workflows.Step",
  field: "impl",
  key: "decision",
  label: "Decision",
  category: "Control",
  icon: "check",
  registry_setting: "ANGEE_WORKFLOW_STEP_CLASSES",
  class_path: "angee.workflows.steps.DecisionStep",
  base_class_path: "angee.workflows.steps.StepImpl",
  addon_id: "angee.workflows",
  addon_label: "Workflows",
  description: "Wait for a registered decision.",
  defaults: { config: { explicit_flag: true } },
  config_schema: {
    properties: {
      explicit_flag: { type: "boolean", label: "Explicit flag", omittable: true },
      schema_flag: { type: "boolean", label: "Schema flag", defaultValue: true, omittable: true },
      unset_flag: { type: "boolean", label: "Unset flag", omittable: true },
    },
  },
  source: null,
  source_file: null,
  source_start_line: null,
  source_unavailable_reason: "Source is packaged without Python files.",
};

function renderDetail(): void {
  render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ImplementationDetail />
    </AppRuntimeProvider>,
  );
}

beforeEach(() => {
  mocks.query = {
    data: { platform_implementation: implementation },
    error: null,
    isPending: false,
  };
  mocks.routeHref.mockClear();
});

afterEach(() => cleanup());

describe("ImplementationDetail", () => {
  test("shows only truthful read-only defaults", () => {
    renderDetail();
    fireEvent.click(screen.getByRole("tab", { name: "implementation.settings" }));

    expect(screen.getByText("Explicit flag")).toBeTruthy();
    expect(screen.getByText("Schema flag")).toBeTruthy();
    expect(screen.getByText("Unset flag")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Explicit flag" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("checkbox", { name: "Schema flag" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByRole("checkbox", { name: "Unset flag" })).toBeNull();
    expect(screen.getByText("implementation.noDefault")).toBeTruthy();
  });

  test("distinguishes unavailable source from a query failure", () => {
    renderDetail();
    fireEvent.click(screen.getByRole("tab", { name: "implementation.code" }));
    expect(screen.getByText("Source is packaged without Python files.")).toBeTruthy();

    cleanup();
    mocks.query = { data: undefined, error: new Error("Access denied"), isPending: false };
    renderDetail();
    expect(screen.getByText("implementation.loadError")).toBeTruthy();
    expect(screen.getByText("Access denied")).toBeTruthy();
  });

  test("registers list and record routes under the platform implementation scope", () => {
    const routes = platform.routes ?? [];
    expect(routes).toEqual(expect.arrayContaining([
      expect.objectContaining({
        name: "platform.implementations",
        path: "/platform/implementations",
        resource: "platform.Implementation",
      }),
      expect.objectContaining({
        name: "platform.implementations.record",
        path: "/platform/implementations/$id",
        parent: "platform.implementations",
        menu: "platform.implementations",
      }),
    ]));
  });
});
