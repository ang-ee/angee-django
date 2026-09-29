// @vitest-environment happy-dom

import type { Row } from "@angee/metadata";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { DialogForm } from "../../fragments/DialogForm";
import { RecordActionBar } from "./RecordActionBar";
import { RecordActionTrigger } from "./RecordActionMenu";
import { createUiTestProviders } from "../../testing";
import { RecordChromeProvider } from "../resource/record-chrome-context";
import { AppRuntimeProvider } from "../../runtime";

const { Provider, clearClients } = createUiTestProviders({
  queryClientConfig: { defaultOptions: {
    mutations: { retry: false }, queries: { retry: false },
  } },
});

describe("RecordActionBar", () => {
  test("contributed descriptors compose into the existing Actions menu", async () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={record} actions={[]} contributedActions={
      <RecordActionBar record={record} actions={[
        { id: "publish", label: "Publish", run },
        { id: "hidden", label: "Hidden", run, visibleWhen: () => false },
      ]} />
    } />);
    expect(screen.getAllByRole("button", { name: "Actions" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(screen.queryByRole("menuitem", { name: "Hidden" })).toBeNull();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Publish" }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(run.mock.calls[0]?.[0].record).toEqual(record);
  });
  test("primary contributed descriptors inherit the saved form's dirty gate", () => {
    const run = vi.fn();
    renderActionBar(<RecordChromeProvider value={{ resource: "example.Item", canonicalResource: "example.Item",
      recordId: "item-1", dataProviderName: "console", record, formReadOnly: false, actionsBlocked: true }}>
      <RecordActionBar record={record} actions={[{ id: "open", label: "Open", placement: "toolbar", run }]} />
    </RecordChromeProvider>);
    const button = screen.getByRole("button", { name: "Open" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(run).not.toHaveBeenCalled();
  });
  test("toolbar actions use the same confirmation and form blocking as menu actions", async () => {
    const run = vi.fn();
    const action = { id: "archive", label: "Archive", placement: "toolbar" as const, run,
      confirm: { title: "Archive record?" } };
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[action]} blocked />);
    expect((screen.getByRole("button", { name: "Archive" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    cleanup();
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()} actions={[action]} />);
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    await screen.findByText("Archive record?");
    expect(run).not.toHaveBeenCalled();
  });
  test("preview keeps allowed verbs and delete visible but blocks click and keyboard activation", async () => {
    const run = vi.fn();
    const onDelete = vi.fn();
    renderActionBar(<AppRuntimeProvider runtime={{ auth: {
      user: { id: "person", name: "Person" }, status: "authenticated", hasRole: () => false,
      viewAs: { viewAs: { userId: "person" }, currentUser: { id: "person", name: "Person" }, realUser: null,
        viewablePeople: [], enter: vi.fn(), exit: vi.fn() },
    } }}>
      <RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
        actions={[{ id: "allowed", label: "Allowed verb", run }, { id: "forbidden", label: "Forbidden verb", run, visibleWhen: () => false }]}
        deleteAction={{ canDelete: true, isPending: false, onDelete }} />
    </AppRuntimeProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    for (const name of ["Allowed verb", "Delete"]) {
      const item = await screen.findByRole("menuitem", { name });
      expect(item.getAttribute("aria-disabled")).toBe("true");
      fireEvent.click(item);
      fireEvent.keyDown(item, { key: "Enter" });
      fireEvent.keyDown(item, { key: " " });
    }
    expect(screen.queryByRole("menuitem", { name: "Forbidden verb" })).toBeNull();
    expect(run).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });
  afterEach(() => {
    cleanup();
    clearClients();
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
