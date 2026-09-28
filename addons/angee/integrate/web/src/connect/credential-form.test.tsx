// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ActionContext, RecordActionRunner, UseRecordActionOptions } from "@angee/ui";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fields: [] as Array<Record<string, unknown>>,
  revealCredential: vi.fn(async () => ({ reveal_credential: { secret: "test-secret" } })),
  useAuthoredMutation: vi.fn(),
  useRecordAction: vi.fn<(run: RecordActionRunner, options?: UseRecordActionOptions) => void>(),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: mocks.useAuthoredMutation,
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Field: (props: Record<string, unknown>) => {
    mocks.fields.push(props);
    return null;
  },
  Form: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Group: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  useRecordAction: mocks.useRecordAction,
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
import { IntegrateRevealCredential } from "./documents";

interface FieldLike {
  name: string;
  options?: ReadonlyArray<{ value: string; label: string }>;
  showWhen?: (values: Record<string, unknown>) => boolean;
}

describe("credentialCreateForm", () => {
  beforeEach(() => {
    mocks.fields = [];
    mocks.revealCredential.mockClear();
    mocks.useRecordAction.mockClear();
    mocks.useAuthoredMutation.mockReset();
    mocks.useAuthoredMutation.mockReturnValue([
      mocks.revealCredential,
      { fetching: false, error: null },
    ]);
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

  test("reveals a transient secret in a copyable read-only prompt without refreshing", async () => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id="credential-1" />);

    expect(mocks.useAuthoredMutation).toHaveBeenCalledWith(
      IntegrateRevealCredential,
      { transient: true },
    );
    expect(mocks.useRecordAction).toHaveBeenCalledWith(
      expect.any(Function),
      { refresh: false },
    );
    const runner = mocks.useRecordAction.mock.calls[0]?.[0];
    if (!runner) throw new Error("Reveal action was not registered.");
    const prompt = vi.fn<ActionContext["prompt"]>().mockResolvedValue(null);
    const refresh = vi.fn();
    await expect(runner("credential-1", {
      record: { id: "credential-1" },
      values: {},
      refresh,
      update: vi.fn<ActionContext["update"]>().mockResolvedValue(null),
      prompt,
    })).resolves.toBeUndefined();

    expect(mocks.revealCredential).toHaveBeenCalledWith({ id: "credential-1" });
    expect(prompt).toHaveBeenCalledOnce();
    expect(prompt).toHaveBeenCalledWith({
      title: "credentials.reveal.title",
      body: "credentials.reveal.body",
      fields: [{
        name: "secret",
        label: "credentials.reveal.secretLabel",
        defaultValue: "test-secret",
        readOnly: true,
        copyable: true,
      }],
    });
    expect(refresh).not.toHaveBeenCalled();
  });
});
