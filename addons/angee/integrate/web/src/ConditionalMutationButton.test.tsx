// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chrome: {
    resource: "messaging.Channel",
    canonicalResource: "integrate.Integration",
    dataProviderName: "console",
    recordId: "int_1",
    record: { lifecycle: "CONNECTED" },
  } as {
    resource: string;
    canonicalResource: string;
    dataProviderName: string;
    recordId: string;
    record: Record<string, unknown> | null;
  },
  // `useRecordChromeActionMutation` (the `@angee/ui` owner) maps invalidation from
  // the chrome context, fires the verb and settles its outcome; `record-action.test`
  // there owns that ceremony. This asserts what the button declares to it.
  actions: new Map<string, ReturnType<typeof vi.fn>>(),
  // The raw-outcome sibling a typed-args verb fires through; the dialog owns
  // settling what it resolves.
  outcomes: new Map<string, ReturnType<typeof vi.fn>>(),
  dialogs: [] as Record<string, unknown>[],
  fetching: false,
  // The confirm gate resolves true by default; a test that asserts the refusal
  // path overrides it.
  confirm: vi.fn(async (_options: Record<string, unknown>) => true),
}));

vi.mock("@angee/ui", () => ({
  ActionFormDialog: (props: Record<string, unknown>) => {
    mocks.dialogs.push(props);
    return props.open ? <div role="dialog">args form</div> : null;
  },
  Button: ({ children, loading: _loading, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  useConfirm: () => mocks.confirm,
  useRecordChromeContext: () => mocks.chrome,
  useRecordChromeActionMutation: (field: string) => {
    const action = mocks.actions.get(field) ?? vi.fn(async () => undefined);
    mocks.actions.set(field, action);
    return [action, { fetching: mocks.fetching, error: null }];
  },
  useRecordChromeActionOutcome: (field: string) => {
    const action = mocks.outcomes.get(field) ?? vi.fn(async () => ({ ok: true, message: "done" }));
    mocks.outcomes.set(field, action);
    return [action, { fetching: false, error: null }];
  },
}));

import { ConditionalMutationButton } from "./ConditionalMutationButton";

describe("ConditionalMutationButton", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.chrome = {
      resource: "messaging.Channel",
      canonicalResource: "integrate.Integration",
      dataProviderName: "console",
      recordId: "int_1",
      record: { lifecycle: "CONNECTED" },
    };
    mocks.actions.clear();
    mocks.outcomes.clear();
    mocks.dialogs.length = 0;
    mocks.fetching = false;
    mocks.confirm.mockClear();
    mocks.confirm.mockResolvedValue(true);
  });

  test("renders only when its record predicate matches", () => {
    const when = vi.fn(({ record }) => record.lifecycle === "CONNECTED");
    const { rerender } = render(
      <ConditionalMutationButton
        field="pause_integration"
        label="Pause"
        when={when}
      />,
    );

    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect(when).toHaveBeenCalledWith(expect.objectContaining({
      resource: "messaging.Channel",
      recordId: "int_1",
      canonicalResource: "integrate.Integration",
      dataProviderName: "console",
    }));

    mocks.chrome.record = { lifecycle: "PAUSED" };
    rerender(
      <ConditionalMutationButton
        field="pause_integration"
        label="Pause"
        when={when}
      />,
    );
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  });

  test("fires its verb against the open record id", async () => {
    // Dispatch, invalidation and outcome settling belong to the shared
    // `useRecordChromeActionMutation` owner; this button only chooses the field
    // and the record it targets.
    render(
      <ConditionalMutationButton
        field="pause_integration"
        label="Pause"
        when={() => true}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() =>
      expect(mocks.actions.get("pause_integration")).toHaveBeenCalledWith("int_1"),
    );
  });

  test("does not render before the record has loaded", () => {
    mocks.chrome.record = null;
    render(
      <ConditionalMutationButton
        field="pause_integration"
        label="Pause"
        when={() => true}
      />,
    );

    expect(screen.queryByRole("button")).toBeNull();
  });

  test("gates a confirmed verb behind the shared confirm owner", async () => {
    render(
      <ConditionalMutationButton
        field="mark_integration_disconnected"
        label="Disconnect"
        when={() => true}
        confirm={{ title: "Disconnect this integration?", body: "It stops syncing.", danger: true }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() =>
      expect(mocks.actions.get("mark_integration_disconnected")).toHaveBeenCalledWith("int_1"),
    );
    // The button's own label doubles as the confirm's action label.
    expect(mocks.confirm).toHaveBeenCalledWith({
      title: "Disconnect this integration?",
      body: "It stops syncing.",
      danger: true,
      confirm: "Disconnect",
    });
  });

  test("does not fire a confirmed verb the user declined", async () => {
    mocks.confirm.mockResolvedValue(false);
    render(
      <ConditionalMutationButton
        field="mark_integration_disconnected"
        label="Disconnect"
        when={() => true}
        confirm={{ title: "Disconnect this integration?" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    expect(mocks.actions.get("mark_integration_disconnected")).not.toHaveBeenCalled();
  });

  test("rejects a destructive verb that declares no confirm", () => {
    // A type-level assertion, because the contract is one: the runtime tests above
    // only exercise a `confirm` that was passed, so nothing there fails if
    // `ConditionalMutationButtonProps` is widened back to
    // `{ variant?: ButtonVariant; confirm?: ActionConfirm }` — the regression the
    // union exists to prevent (`disconnect_whatsapp_channel` shipped exactly that
    // cheaper click). `@ts-expect-error` fails `pnpm run typecheck` the moment the
    // union stops rejecting this element.
    const bare = (
      // @ts-expect-error `variant="danger"` requires `confirm`.
      <ConditionalMutationButton
        field="mark_integration_disconnected"
        label="Disconnect"
        when={() => true}
        variant="danger"
      />
    );

    expect(bare.props.variant).toBe("danger");
  });

  test("fires an unconfirmed verb without asking", async () => {
    render(
      <ConditionalMutationButton
        field="pause_integration"
        label="Pause"
        when={() => true}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() =>
      expect(mocks.actions.get("pause_integration")).toHaveBeenCalledWith("int_1"),
    );
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  test("a typed-args verb opens the shared args form instead of firing on click", async () => {
    const args = [
      { name: "username", label: "Username" },
      { name: "password", label: "Password", widget: "password" },
    ];
    render(
      <ConditionalMutationButton
        field="update_imap_channel_credential"
        label="Update credential"
        when={() => true}
        args={args}
      />,
    );

    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Update credential" }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    // The id-only verb never fires: the dialog collects the args and submits them
    // beside the record id through the raw-outcome hook.
    expect(mocks.actions.get("update_imap_channel_credential")).not.toHaveBeenCalled();
    const dialog = mocks.dialogs.at(-1) as {
      action: { id: string; args: unknown; submit: (values: Record<string, unknown>) => Promise<unknown> };
      context: { record: unknown; selectedIds: readonly string[] };
    };
    expect(dialog.action).toMatchObject({ id: "update_imap_channel_credential", args });
    expect(dialog.context).toEqual({ record: mocks.chrome.record, selectedIds: ["int_1"] });

    await expect(dialog.action.submit({ username: "ada", password: "pw" })).resolves.toEqual({
      ok: true,
      message: "done",
    });
    expect(mocks.outcomes.get("update_imap_channel_credential")).toHaveBeenCalledWith("int_1", {
      username: "ada",
      password: "pw",
    });
  });
});
