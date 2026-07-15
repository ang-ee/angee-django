// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  resourceMutationOptions: null as unknown,
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Column: () => null,
  Facet: () => null,
  Field: () => null,
  Form: ({ children }: { children?: ReactNode }) => <>{children}</>,
  Group: ({ children }: { children?: ReactNode }) => <>{children}</>,
  List: ({ children }: { children?: ReactNode }) => <>{children}</>,
  ResourceList: ({ children }: { children?: ReactNode }) => <>{children}</>,
  useAuthoredResourceMutation: (_document: unknown, options: unknown) => {
    mocks.resourceMutationOptions = options;
    return [vi.fn(), { fetching: false, error: null }];
  },
  useEnumOptions: () => [],
  useImplPrefill: () => undefined,
  useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: () => [vi.fn(), { fetching: false, error: null }],
}));

vi.mock("@angee/integrate", () => ({
  canConnectRecord: () => false,
  ConnectOAuthButton: () => null,
}));

vi.mock("../i18n", () => ({
  useAgentsT: () => (key: string) => key,
}));

import { InferenceProvidersPage } from "./InferencePage";

describe("InferenceProvidersPage", () => {
  test("routes provider update invalidation through the resource owner", () => {
    render(<InferenceProvidersPage />);

    expect(mocks.resourceMutationOptions).toEqual({
      invalidateModels: ["agents.InferenceProvider", "agents.InferenceModel"],
    });
  });
});
