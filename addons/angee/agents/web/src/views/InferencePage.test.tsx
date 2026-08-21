// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";
import agents from "../index";

const mocks = vi.hoisted(() => ({
  resourceMutationOptions: null as unknown,
  connectNext: "",
  routeHref: vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Action: () => null,
  Column: () => null,
  Facet: () => null,
  Field: () => null,
  Form: ({ children }: { children?: ReactNode }) => <>{children}</>,
  Group: ({ children }: { children?: ReactNode }) => <>{children}</>,
  List: ({ children }: { children?: ReactNode }) => <>{children}</>,
  ResourceList: ({
    cardActions,
    children,
  }: {
    cardActions?: (
      row: Record<string, unknown>,
      context: { refresh: () => void },
    ) => ReactNode;
    children?: ReactNode;
  }) => (
    <>
      {cardActions?.({ id: "provider-1" }, { refresh: vi.fn() })}
      {children}
    </>
  ),
  useAuthoredResourceMutation: (_document: unknown, options: unknown) => {
    mocks.resourceMutationOptions = options;
    return [vi.fn(), { fetching: false, error: null }];
  },
  useEnumOptions: () => [],
  useImplPrefill: () => undefined,
  useRouteHref: () => mocks.routeHref,
  useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [vi.fn(), { fetching: false, error: null }],
}));

vi.mock("@angee/integrate", () => ({
  canConnectRecord: () => true,
  ConnectOAuthButton: ({ next }: { next: string }) => {
    mocks.connectNext = next;
    return null;
  },
}));

vi.mock("../i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../i18n")>()),
  useAgentsT: () => (key: string) => key,
}));

import { InferenceProvidersPage } from "./InferencePage";

describe("InferenceProvidersPage", () => {
  test("passes the composed providers route to OAuth return navigation", () => {
    const routeHref = createRouteHref(agents.routes ?? []);
    mocks.routeHref.mockImplementation(routeHref);
    Object.assign(mocks.routeHref, { maybe: routeHref.maybe });

    render(<InferenceProvidersPage />);

    expect(mocks.routeHref).toHaveBeenCalledWith("agents.providers");
    expect(mocks.connectNext).toBe("/agents/providers");
  });

  test("routes provider update invalidation through the resource owner", () => {
    const routeHref = createRouteHref(agents.routes ?? []);
    mocks.routeHref.mockImplementation(routeHref);
    Object.assign(mocks.routeHref, { maybe: routeHref.maybe });
    render(<InferenceProvidersPage />);

    expect(mocks.resourceMutationOptions).toEqual({
      invalidateModels: ["agents.InferenceProvider", "agents.InferenceModel"],
    });
  });
});
