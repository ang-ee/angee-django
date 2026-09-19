// @vitest-environment happy-dom

import type { Row } from "@angee/metadata";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { DialogForm } from "../../fragments/DialogForm";
import { RecordActionBar } from "./RecordActionBar";
import { RecordActionTrigger } from "./RecordActionMenu";

describe("RecordActionBar", () => {
  afterEach(() => {
    cleanup();
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
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ModalsHost>
        <ToastProvider>{children}</ToastProvider>
      </ModalsHost>
    </QueryClientProvider>,
  );
}
