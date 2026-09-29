// @vitest-environment happy-dom

import type { Row } from "@angee/metadata";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { DialogForm } from "../../fragments/DialogForm";
import { RecordActionBar } from "./RecordActionBar";
import { RecordActionTrigger } from "./RecordActionMenu";
import { createUiTestProviders } from "../../testing";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import type { ActionDescriptor } from "../page";
import { jsonSchemaActionArgs } from "./json-schema";

const { Provider, clearClients } = createUiTestProviders({
  queryClientConfig: { defaultOptions: {
    mutations: { retry: false }, queries: { retry: false },
  } },
});

describe("RecordActionBar", () => {
  afterEach(() => {
    cleanup();
    clearClients();
  });

  test("promotes declared primary actions while retaining other verbs in overflow", async () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[
        { id: "review", label: "Review", primary: true, run },
        { id: "archive", label: "Archive", run },
        { id: "hidden", label: "Hidden", primary: true, run, visibleWhen: () => false },
      ]} />);
    expect(screen.getByRole("button", { name: "Review" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Hidden" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Review" })).toBeNull();
  });

  test("primary actions share confirmation, pending feedback and success settling", async () => {
    let finish!: (message: string) => void;
    const run = vi.fn(() => new Promise<string>((resolve) => { finish = resolve; }));
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[{ id: "cancel", label: "Cancel task", primary: true, danger: true, run,
        confirm: { title: "Cancel task", body: "Close the pending task?", danger: true } }]} />);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel task" }));
    const confirmation = await screen.findByRole("alertdialog", { name: "Cancel task" });
    expect(run).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel task" }));
    await waitFor(() => expect(run).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel task" }).getAttribute("aria-busy")).toBe("true"));
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Cancel task" }).disabled).toBe(true);
    await act(async () => { finish("Pending task closed."); });
    expect(await screen.findByText("Pending task closed.")).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Cancel task" }).disabled).toBe(false);
  });

  test("primary actions respect explicit disabling and the form's blocked state", () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      blocked actions={[{ id: "review", label: "Review", primary: true, run }]} />);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Review" }).disabled).toBe(true);
    cleanup();
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[{ id: "review", label: "Review", primary: true, disabled: true, run }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(run).not.toHaveBeenCalled();
  });

  test("a primary action opens the existing typed-args form directly", async () => {
    const submit = vi.fn(async () => ({ ok: true, message: "Review recorded." }));
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[{ id: "review", label: "Review", primary: true, args: [], submit }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    const dialog = await screen.findByRole("dialog", { name: "Review" });
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({}, expect.objectContaining({ record, selectedIds: [record.id] })));
    expect(await screen.findByText("Review recorded.")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Review" })).toBeNull();
  });

  test("keeps a schema draft across refresh and submits with the latest record revision", async () => {
    const submit = vi.fn<NonNullable<ActionDescriptor["submit"]>>(async (_values, context) => {
      if (context.record?.revision === 1) {
        await context.refresh?.();
        return { ok: false, message: "Correct the answer.", validationErrors: { reason: ["Try again."] } };
      }
      return { ok: true, message: "Recorded." };
    });
    // The declaration reads the loaded record only to establish the opening seed.
    const definition = vi.fn(() => jsonSchemaActionArgs({ type: "object", properties: {
      reason: { type: "string", label: "Reason", default: "Initial" },
    } }, defaultWidgets));
    function RefreshingRecord(): React.ReactElement {
      const [record, setRecord] = React.useState<Row>({ id: "note-1", revision: 1 });
      return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><RecordActionBar
        record={record}
        actions={[{ id: "review", label: "Review", args: definition, submit }]}
        applyPatch={vi.fn()}
        reload={async () => { const next = { id: "note-1", revision: 2 }; setRecord(next); return next; }}
      /></AppRuntimeProvider>;
    }
    renderActionBar(<RefreshingRecord />);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Review" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Reason" }), { target: { value: "Retained draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    await screen.findByText("Try again.");
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "Review" }).disabled).toBe(false));
    expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Reason" }).value).toBe("Retained draft");
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    await screen.findByText("Recorded.");
    expect(submit.mock.calls.map(([, context]) => context.record?.revision)).toEqual([1, 2]);
    expect(definition).toHaveBeenCalledTimes(1);
  });

  test("settles an open args dialog after invalidation hides its action", async () => {
    let hideAction!: () => void;
    let resolve!: (value: { ok: boolean; message: string }) => void;
    const submit = vi.fn(() => new Promise<{ ok: boolean; message: string }>((done) => { resolve = done; }));
    function Record(): React.ReactElement {
      const [record, setRecord] = React.useState<Row>({ id: "note-1", can_act: true });
      hideAction = () => setRecord({ ...record, can_act: false });
      return <RecordActionBar record={record} reload={vi.fn()} applyPatch={vi.fn()}
        actions={[{ id: "review", label: "Review", args: [], submit, visibleWhen: (row) => row.can_act === true }]} />;
    }
    renderActionBar(<Record />);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Review" }));
    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    act(hideAction);
    expect(screen.getByRole("dialog", { name: "Review" })).toBeTruthy();
    await act(async () => { resolve({ ok: true, message: "Review accepted." }); });
    expect(await screen.findByText("Review accepted.")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Review" })).toBeNull();
  });

  test("clears trigger loading after a run action resolves in StrictMode", async () => {
    let resolveAction!: () => void;
    const run = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveAction = resolve;
        }),
    );

    renderActionBar(
      <React.StrictMode>
        <RecordActionBar
          record={record}
          actions={[{ id: "sync", label: "Sync", run }]}
          applyPatch={vi.fn()}
          reload={vi.fn()}
        />
      </React.StrictMode>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sync" }));

    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Actions" }).getAttribute("aria-busy"),
      ).toBe("true"),
    );

    resolveAction();

    await waitFor(() => {
      const trigger = screen.getByRole("button", { name: "Actions" });
      expect(trigger.getAttribute("aria-busy")).toBeNull();
      expect((trigger as HTMLButtonElement).disabled).toBe(false);
    });
  });

  test("clears trigger loading after a run action rejects", async () => {
    const run = vi.fn(async () => {
      throw new Error("Provider rejected the authorization code.");
    });

    renderActionBar(
      <React.StrictMode>
        <RecordActionBar
          record={record}
          actions={[{ id: "sync", label: "Sync", run }]}
          applyPatch={vi.fn()}
          reload={vi.fn()}
        />
      </React.StrictMode>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sync" }));

    expect(
      await screen.findAllByText("Provider rejected the authorization code."),
    ).not.toHaveLength(0);
    await waitFor(() => {
      const trigger = screen.getByRole("button", { name: "Actions" });
      expect(trigger.getAttribute("aria-busy")).toBeNull();
      expect((trigger as HTMLButtonElement).disabled).toBe(false);
    });
  });

  test("keeps a contributed action mounted when its menu closes for a dialog", async () => {
    let triggerNode: HTMLElement | null = null;
    renderActionBar(
      <RecordActionBar
        record={record}
        actions={[]}
        applyPatch={vi.fn()}
        reload={vi.fn()}
        contributedActions={
          <DialogActionProbe onTriggerRef={(node) => { triggerNode = node; }} />
        }
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    const menuItem = await screen.findByRole("menuitem", {
      name: "Update credential",
    });
    expect(triggerNode).toBe(menuItem);
    expect(menuItem.getAttribute("aria-haspopup")).toBe("dialog");
    fireEvent.click(menuItem);

    expect(await screen.findByRole("dialog", { name: "Credential form" })).toBeTruthy();
    expect(
      screen.queryByRole("menuitem", { name: "Update credential" }),
    ).toBeNull();
    const closedItem = screen.getByRole("menuitem", {
      name: "Update credential",
      hidden: true,
    });
    expect(closedItem.getAttribute("tabindex")).toBe("-1");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Actions" }),
      );
    });
  });
});

const record: Row = { id: "note-1" };

function DialogActionProbe({
  onTriggerRef,
}: {
  onTriggerRef: (node: HTMLElement | null) => void;
}): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return (
    <DialogForm
      open={open}
      onOpenChange={setOpen}
      title="Credential form"
      trigger={
        <RecordActionTrigger ref={onTriggerRef} data-testid="credential-trigger">
          Update credential
        </RecordActionTrigger>
      }
    >
      <label htmlFor="username">Username</label>
      <input id="username" />
    </DialogForm>
  );
}

function renderActionBar(children: React.ReactElement): void {
  render(
    <Provider>
      <ModalsHost>
        <ToastProvider>{children}</ToastProvider>
      </ModalsHost>
    </Provider>,
  );
}
