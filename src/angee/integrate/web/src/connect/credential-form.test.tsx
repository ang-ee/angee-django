// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ActionFormDialog, AppRuntimeProvider, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { ActionContext, ActionDescriptor, FieldDescriptor, FormSubmit, RecordActionRunner, UseRecordActionOptions } from "@angee/ui";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fields: [] as Array<Record<string, unknown>>,
  actions: [] as ActionDescriptor[],
  revealCredential: vi.fn(async () => ({ reveal_credential: { secret: "test-secret" } })),
  reveal: vi.fn(async () => undefined),
  useAuthoredMutation: vi.fn(),
  createCredential: vi.fn(),
  replaceSecret: vi.fn(),
  useAuthoredResourceMutation: vi.fn(),
  useActionOutcomeMutation: vi.fn(),
  createSubmit: undefined as FormSubmit | undefined,
  useRecordAction: vi.fn<(run: RecordActionRunner, options?: UseRecordActionOptions) => void>(),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: mocks.useAuthoredMutation,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Action: (action: ActionDescriptor) => { mocks.actions.push(action); return null; },
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
  useActionOutcomeMutation: mocks.useActionOutcomeMutation,
  useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
  useSecretReveal: () => mocks.reveal,
}));

import { credentialCreateForm } from "./credential-form";
import { IntegrateCreateCredential, IntegrateRevealCredential } from "./documents";

const { Provider, clearClients } = createUiTestProviders({ resources: [] });
afterEach(() => { cleanup(); clearClients(); });

interface FieldLike {
  name: string;
  widget?: string;
  options?: ReadonlyArray<{ value: string; label: string }>;
  showWhen?: (values: Record<string, unknown>) => boolean;
}

describe("credentialCreateForm", () => {
  beforeEach(() => {
    mocks.fields = [];
    mocks.actions = [];
    mocks.createSubmit = undefined;
    mocks.createCredential.mockReset().mockResolvedValue({ create_credential: { id: "credential-1" } });
    mocks.replaceSecret.mockReset().mockResolvedValue({ ok: true, message: "Replaced." });
    mocks.useAuthoredResourceMutation.mockReset().mockReturnValue([mocks.createCredential, {}]);
    mocks.useActionOutcomeMutation.mockReset().mockReturnValue([mocks.replaceSecret, {}]);
    mocks.revealCredential.mockClear();
    mocks.useRecordAction.mockClear();
    mocks.useAuthoredMutation.mockReset();
    mocks.useAuthoredMutation.mockReturnValue([
      mocks.revealCredential,
      { fetching: false, error: null },
    ]);
  });

  test("offers static-token, ssh-key and jwt-bearer kinds and swaps the material field", () => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id={null} />);
    const fields = new Map(
      mocks.fields.map((field) => [field.name, field as unknown as FieldLike]),
    );

    expect([...fields.keys()]).toEqual(["name", "kind", "api_key", "private_key", "key_file"]);
    expect(fields.get("kind")?.options?.map((option) => option.value)).toEqual([
      "static_token",
      "ssh_key",
      "jwt_bearer",
    ]);
    expect(fields.get("api_key")?.showWhen?.({ kind: "static_token" })).toBe(true);
    expect(fields.get("api_key")?.showWhen?.({ kind: "ssh_key" })).toBe(false);
    // A static token is a one-line secret, so it is entered in the masked password widget (whose
    // rendering its own test proves); a private key or key file is multi-line, entered in a text area.
    expect(fields.get("api_key")?.widget).toBe("password");
    expect(fields.get("private_key")?.widget).toBe("textarea");
    expect(fields.get("key_file")?.widget).toBe("textarea");
    expect(fields.get("private_key")?.showWhen?.({ kind: "ssh_key" })).toBe(true);
    expect(fields.get("private_key")?.showWhen?.({ kind: "static_token" })).toBe(false);
    expect(fields.get("key_file")?.showWhen?.({ kind: "jwt_bearer" })).toBe(true);
    expect(fields.get("key_file")?.showWhen?.({ kind: "ssh_key" })).toBe(false);
    for (const [name, widget] of [["api_key", "password"], ["private_key", "textarea"], ["key_file", "textarea"]]) {
      expect(fields.get(name!)).toMatchObject({ widget, autoComplete: "new-password" });
    }
  });

  test("edits only projected name metadata, always leaving expiry read-only", () => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id="credential-1" />);
    const field = mocks.fields.find((field) => field.name === "name") as unknown as FieldDescriptor;
    expect(field.resolve?.({ id: "credential-1", editable_fields: ["name"] })).toMatchObject({ name: "name", readOnly: false });
    expect(field.resolve?.({ id: "credential-1", editable_fields: [] })).toMatchObject({ name: "name", readOnly: true });
    expect(field.resolve?.({ id: "credential-1" })).toMatchObject({ name: "name", readOnly: true });
    expect(mocks.fields).toContainEqual(expect.objectContaining({ name: "expires_at", readOnly: true }));
    render(<Component resource={credentialCreateForm.resource} id="credential-2" />);
    expect(mocks.fields.filter((field) => field.name === "name").at(-1)?.resolve).toBe(field.resolve);
    expect(mocks.fields).toContainEqual(expect.objectContaining({ name: "editable_fields", hidden: true, readOnly: true }));
  });

  test.each([
    ["api_key"], ["username", "password"], ["app_id", "app_secret"], ["private_key"], ["key_file"],
  ])("replaces only the server-projected material fields (%j), with confirmation and transient inputs", async (...names) => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id="credential-1" />);
    const action = mocks.actions.find(({ id }) => id === "replace-secret")!;
    const record = { id: "credential-1", replacement_fields: names };
    expect(action).toMatchObject({ placement: "menu", danger: true, confirm: { danger: true } });
    expect(action.visibleWhen?.(record)).toBe(true);
    expect(action.visibleWhen?.({ id: "oauth", replacement_fields: [] })).toBe(false);
    const args = typeof action.args === "function" ? action.args({ record, selectedIds: [record.id] }) : action.args;
    const expectedWidgets: Record<string, string> = {
      api_key: "password", username: "text", password: "password", app_id: "text", app_secret: "password",
      private_key: "textarea", key_file: "textarea",
    };
    expect(Array.isArray(args) ? args.map(({ name, widget }) => [name, widget]) : []).toEqual(names.map((name) => [name, expectedWidgets[name]]));
    for (const arg of Array.isArray(args) ? args : []) {
      expect(arg.optional).not.toBe(true);
      expect(arg.autoComplete).toBe(expectedWidgets[arg.name] === "text" ? undefined : "new-password");
    }
    const material = Object.fromEntries(names.map((name) => [name, `entered-${name}`]));
    expect(await action.submit?.({ ...material, unexpected: "discarded" }, { record, selectedIds: [record.id] }))
      .toEqual({ ok: true, message: "Replaced." });
    expect(mocks.replaceSecret).toHaveBeenCalledExactlyOnceWith(record.id, { confirmed: true, data: material });
    expect(mocks.useActionOutcomeMutation).toHaveBeenCalledWith("replace_credential_secret",
      { transient: true, errorNotification: false, invalidateModels: ["integrate.Credential"] });
  });

  test.each(["private_key", "key_file"])("preserves multiline %s all the way to the mutation", async (name) => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id="credential-1" />);
    const action = mocks.actions.find(({ id }) => id === "replace-secret")!;
    const material = "first line\nsecond line\nthird line\n";
    const record = { id: "credential-1", replacement_fields: [name] };
    render(<Provider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ToastProvider>
      <ActionFormDialog action={action} context={{ record, selectedIds: [] }} open onOpenChange={vi.fn()} />
    </ToastProvider></AppRuntimeProvider></Provider>);
    const input = screen.getByRole<HTMLTextAreaElement>("textbox");
    expect(input.tagName).toBe("TEXTAREA");
    fireEvent.change(input, { target: { value: material } });
    fireEvent.click(screen.getByRole("button", { name: "Replace secret" }));
    await waitFor(() => expect(mocks.replaceSecret).toHaveBeenCalledExactlyOnceWith(record.id, { confirmed: true, data: { [name]: material } }));
  });

  test("preserves the handler's field refusal in the replacement form", async () => {
    mocks.replaceSecret.mockResolvedValue({
      ok: false, message: "Password required.", validationErrors: { password: ["Required."] },
    });
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id="credential-1" />);
    const action = mocks.actions.find(({ id }) => id === "replace-secret")!;
    expect(await action.submit?.({}, { record: { id: "credential-1", replacement_fields: ["username", "password"] }, selectedIds: [] }))
      .toMatchObject({ ok: false, validationErrors: { password: ["Required."] } });
  });

  test("reveals a transient secret in a copyable read-only prompt without refreshing", async () => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id="credential-1" />);

    expect(mocks.actions.find(({ id }) => id === "reveal")?.placement).toBe("menu");
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
      title: "Credential secret",
      body: "Copy it now — it is shown on request only and never kept in the form.",
      secrets: [{ name: "secret", label: "Secret", value: "test-secret" }],
    });
    expect(refresh).not.toHaveBeenCalled();
  });

  test.each([
    { kind: "static_token", api_key: "", private_key: "", key_file: "" },
    { kind: "ssh_key", api_key: "", private_key: "private material", key_file: "" },
    { kind: "jwt_bearer", api_key: "", private_key: "", key_file: "{\"issuer\": \"robot\"}" },
  ])("creates $kind using canonical input names and form-owned error feedback", async (material) => {
    const Component = credentialCreateForm.Component;
    render(<Component resource={credentialCreateForm.resource} id={null} />);
    // The form renders the failure itself, and the entered secret is not kept in the mutation cache.
    expect(mocks.useAuthoredResourceMutation).toHaveBeenCalledWith(IntegrateCreateCredential,
      expect.objectContaining({ errorNotification: false, transient: true }));
    const data = { name: "New credential", ...material };
    if (!mocks.createSubmit) throw new Error("Create submission was not registered.");
    await expect(mocks.createSubmit(data, {
      resource: credentialCreateForm.resource, id: null, isCreate: true, record: null,
      baselineRecord: null, idempotencyKey: "create-key", lines: null, values: data, baselineValues: {},
    })).resolves.toEqual({ status: "ok", data: { id: "credential-1" } });
    expect(mocks.createCredential).toHaveBeenCalledExactlyOnceWith({ data });
  });
});
