// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ActionContext, FormSubmit, RecordActionRunner, UseRecordActionOptions } from "@angee/ui";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fields: [] as Array<Record<string, unknown>>,
  revealCredential: vi.fn(async () => ({ reveal_credential: { secret: "test-secret" } })),
  reveal: vi.fn(async () => undefined),
  useAuthoredMutation: vi.fn(),
  createCredential: vi.fn(),
  useAuthoredResourceMutation: vi.fn(),
  createSubmit: undefined as FormSubmit | undefined,
  useRecordAction: vi.fn<(run: RecordActionRunner, options?: UseRecordActionOptions) => void>(),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: mocks.useAuthoredMutation,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Action: () => null,
  Field: (props: Record<string, unknown>) => {
    mocks.fields.push(props);
    return null;
  },
  Form: ({ children, createSubmit }: { children?: React.ReactNode; createSubmit?: FormSubmit }) => {
    mocks.createSubmit = createSubmit;
    return <>{children}</>;
  },
  Group: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  useRecordAction: mocks.useRecordAction,
  registerForm: (
    resource: string,
    Component: React.ComponentType<Record<string, unknown>>,
  ) => ({ resource, Component }),
  useAuthoredResourceMutation: mocks.useAuthoredResourceMutation,
  useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
  useSecretReveal: () => mocks.reveal,
}));

vi.mock("../i18n", () => ({
  useIntegrateT: () => (key: string) => key,
}));

import { credentialCreateForm } from "./credential-form";
import { IntegrateCreateCredential, IntegrateRevealCredential } from "./documents";

interface FieldLike {
  name: string;
  options?: ReadonlyArray<{ value: string; label: string }>;
  showWhen?: (values: Record<string, unknown>) => boolean;
}

describe("credentialCreateForm", () => {
  beforeEach(() => {
    mocks.fields = [];
    mocks.createSubmit = undefined;
    mocks.createCredential.mockReset().mockResolvedValue({ create_credential: { id: "credential-1" } });
    mocks.useAuthoredResourceMutation.mockReset().mockReturnValue([
      mocks.createCredential, { fetching: false, error: null },
    ]);
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

    expect([...fields.keys()]).toEqual(["name", "kind", "api_key", "private_key"]);
    expect(fields.get("kind")?.options?.map((option) => option.value)).toEqual([
      "static_token",
      "ssh_key",
    ]);
    expect(fields.get("api_key")?.showWhen?.({ kind: "static_token" })).toBe(true);
    expect(fields.get("api_key")?.showWhen?.({ kind: "ssh_key" })).toBe(false);
    expect(fields.get("private_key")?.showWhen?.({ kind: "ssh_key" })).toBe(true);
    expect(fields.get("private_key")?.showWhen?.({ kind: "static_token" })).toBe(false);
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
    const refresh = vi.fn();
    await expect(runner("credential-1", {
      record: { id: "credential-1" },
      values: {},
      refresh,
      update: vi.fn<ActionContext["update"]>().mockResolvedValue(null),
      prompt: vi.fn<ActionContext["prompt"]>().mockResolvedValue(null),
    })).resolves.toBeUndefined();

    expect(mocks.revealCredential).toHaveBeenCalledWith({ id: "credential-1" });
    // The one-time reveal goes through the shared secret-reveal owner.
    expect(mocks.reveal).toHaveBeenCalledOnce();
    expect(mocks.reveal).toHaveBeenCalledWith({
      title: "credentials.reveal.title",
      body: "credentials.reveal.body",
      secrets: [{ name: "secret", label: "credentials.reveal.secretLabel", value: "test-secret" }],
    });
    expect(refresh).not.toHaveBeenCalled();
  });

  test.each([
    { kind: "static_token", api_key: "", private_key: "" },
    { kind: "ssh_key", api_key: "", private_key: "private material" },
  ])("creates $kind using canonical input names and form-owned error feedback", async (material) => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id={null} />);
    expect(mocks.useAuthoredResourceMutation).toHaveBeenCalledWith(IntegrateCreateCredential,
      expect.objectContaining({ errorNotification: false }));
    const data = { name: "New credential", ...material };
    if (!mocks.createSubmit) throw new Error("Create submission was not registered.");
    await expect(mocks.createSubmit(data, {
      resource: credentialCreateForm.resource, id: null, isCreate: true, record: null,
      baselineRecord: null, idempotencyKey: "create-key", lines: null, values: data, baselineValues: {},
    })).resolves.toEqual({ status: "ok", data: { id: "credential-1" } });
    expect(mocks.createCredential).toHaveBeenCalledExactlyOnceWith({ data });
  });
});
