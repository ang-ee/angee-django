// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fields: [] as Array<Record<string, unknown>>,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: () => [vi.fn(), { fetching: false, error: null }],
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Field: (props: Record<string, unknown>) => {
    mocks.fields.push(props);
    return null;
  },
  Form: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Group: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  recordActionId: () => null,
  registerForm: (
    resource: string,
    Component: React.ComponentType<Record<string, unknown>>,
  ) => ({ resource, Component }),
  useAuthoredResourceMutation: () => [vi.fn(), { fetching: false, error: null }],
  useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
}));

vi.mock("../i18n", () => ({
  useIntegrateT: () => (key: string) => key,
}));

import { credentialCreateForm } from "./credential-form";

interface FieldLike {
  name: string;
  options?: ReadonlyArray<{ value: string; label: string }>;
  showWhen?: (values: Record<string, unknown>) => boolean;
}

describe("credentialCreateForm", () => {
  beforeEach(() => {
    mocks.fields = [];
  });

  test("offers static-token and ssh-key kinds and swaps the material field", () => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id={null} />);
    const fields = new Map(
      mocks.fields.map((field) => [field.name, field as unknown as FieldLike]),
    );

    expect([...fields.keys()]).toEqual(["name", "kind", "apiKey", "privateKey"]);
    expect(fields.get("kind")?.options?.map((option) => option.value)).toEqual([
      "static_token",
      "ssh_key",
    ]);
    expect(fields.get("apiKey")?.showWhen?.({ kind: "static_token" })).toBe(true);
    expect(fields.get("apiKey")?.showWhen?.({ kind: "ssh_key" })).toBe(false);
    expect(fields.get("privateKey")?.showWhen?.({ kind: "ssh_key" })).toBe(true);
    expect(fields.get("privateKey")?.showWhen?.({ kind: "static_token" })).toBe(false);
  });
});
