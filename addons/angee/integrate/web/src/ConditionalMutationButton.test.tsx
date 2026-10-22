// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
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
  // there owns that ceremony. This asserts what the verb declares to it.
  actions: new Map<string, ReturnType<typeof vi.fn>>(),
  outcomes: new Map<string, ReturnType<typeof vi.fn>>(),
  // `RecordVerb` (the `@angee/ui` owner) renders the descriptor through
  // `RecordActionBar`, which owns the confirm, the args form and the feedback.
  verbs: [] as Record<string, unknown>[],
}));

vi.mock("@angee/ui", () => ({
  RecordVerb: ({ action }: { action: Record<string, unknown> }) => {
    mocks.verbs.push(action);
    return <button type="button">{action.label as React.ReactNode}</button>;
  },
  useRecordChromeContext: () => mocks.chrome,
  useRecordChromeActionMutation: (field: string) => {
    const action = mocks.actions.get(field) ?? vi.fn(async () => undefined);
    mocks.actions.set(field, action);
    return [action, { fetching: false, error: null }];
  },
  useRecordChromeActionOutcome: (field: string) => {
    const action = mocks.outcomes.get(field) ?? vi.fn(async () => ({ ok: true, message: "done" }));
    mocks.outcomes.set(field, action);
    return [action, { fetching: false, error: null }];
  },
}));

import { ConditionalMutationButton } from "./ConditionalMutationButton";

type Declared = {
  id: string; label: React.ReactNode; placement: string; danger: boolean; primary: boolean;
  confirm?: unknown; args?: unknown;
  run?: () => Promise<unknown>; submit?: (values: Record<string, unknown>) => Promise<unknown>;
};

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
    mocks.verbs.length = 0;
  });

  test("declares a verb only when its record predicate matches", () => {
    const when = vi.fn(({ record }) => record.lifecycle === "CONNECTED");
    const { rerender } = render(<ConditionalMutationButton field="pause_integration" label="Pause" when={when} />);

    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect(when).toHaveBeenCalledWith(expect.objectContaining({
      resource: "messaging.Channel",
      recordId: "int_1",
      canonicalResource: "integrate.Integration",
      dataProviderName: "console",
    }));

    mocks.chrome.record = { lifecycle: "PAUSED" };
    rerender(<ConditionalMutationButton field="pause_integration" label="Pause" when={when} />);
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  });

  test("does not declare before the record has loaded", () => {
    mocks.chrome.record = null;
    render(<ConditionalMutationButton field="pause_integration" label="Pause" when={() => true} />);
    expect(mocks.verbs).toHaveLength(0);
  });

  test("an id-only verb runs the chrome mutation against the open record", async () => {
    render(<ConditionalMutationButton field="pause_integration" label="Pause" when={() => true} />);
    const declared = mocks.verbs.at(-1) as Declared;
    expect(declared).toMatchObject({ id: "pause_integration", placement: "toolbar", danger: false, primary: false });
    expect(declared.confirm).toBeUndefined();
    await declared.run?.();
    expect(mocks.actions.get("pause_integration")).toHaveBeenCalledWith("int_1");
  });

  test("a confirmed destructive verb hands its copy and tone to the record action bar", () => {
    render(<ConditionalMutationButton field="mark_integration_disconnected" label="Disconnect" when={() => true}
      variant="danger" confirm={{ title: "Disconnect this integration?", body: "It stops syncing.", danger: true }} />);
    expect(mocks.verbs.at(-1)).toMatchObject({
      id: "mark_integration_disconnected", danger: true,
      confirm: { title: "Disconnect this integration?", body: "It stops syncing.", danger: true },
    });
  });

  test("rejects a destructive verb that declares no confirm", () => {
    // A type-level assertion: `@ts-expect-error` fails `pnpm run typecheck` the moment
    // the props union stops rejecting a danger verb without its confirm.
    const bare = (
      // @ts-expect-error `variant="danger"` requires `confirm`.
      <ConditionalMutationButton field="mark_integration_disconnected" label="Disconnect" when={() => true} variant="danger" />
    );
    expect(bare.props.variant).toBe("danger");
  });

  test("a typed-args verb submits the collected values beside the record id", async () => {
    const args = [
      { name: "first_name", label: "First name" },
      { name: "expected_revision", label: "Revision", widget: "number" },
    ];
    render(<ConditionalMutationButton field="rename_user" label="Rename user" when={() => true} args={args} />);
    const declared = mocks.verbs.at(-1) as Declared;
    expect(declared).toMatchObject({ id: "rename_user", args });
    expect(declared.run).toBeUndefined();
    const values = { first_name: "Morgan", expected_revision: 4 };
    await expect(declared.submit?.(values)).resolves.toEqual({ ok: true, message: "done" });
    expect(mocks.outcomes.get("rename_user")).toHaveBeenCalledWith("int_1", values);
    expect(mocks.actions.get("rename_user")).not.toHaveBeenCalled();
  });
});
