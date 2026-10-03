// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { DialogForm } from "../fragments/DialogForm";
import { Dialog } from "../ui/dialog";
import { AppRuntimeProvider, createAngeeI18nInstance } from "../runtime";
import { ActionMenu, ActionTrigger } from "./ActionMenu";

afterEach(cleanup);

describe("ActionMenu", () => {
  test("accepts end alignment and an accessible icon-only trigger", async () => {
    render(<ActionMenu align="end" label={null} aria-label="Commands" size="iconSm">
      <ActionTrigger>Inspect example</ActionTrigger>
    </ActionMenu>);
    const trigger = screen.getByRole("button", { name: "Commands" });
    expect(trigger.textContent).toBe("");
    fireEvent.click(trigger);
    const menu = await screen.findByRole("menu");
    expect(menu.closest("[data-align]")?.getAttribute("data-align")).toBe("end");
  });

  test("uses UI vocabulary and adapts toolbar actions into native menu items", async () => {
    const run = vi.fn();
    render(
      <AppRuntimeProvider runtime={{ i18n: createAngeeI18nInstance({ ui: { "list.actions": "Commands" } }) }}>
        <ActionTrigger onClick={run}>Run inline</ActionTrigger>
        <ActionMenu blocked>
          <ActionTrigger onClick={run}>Run command</ActionTrigger>
        </ActionMenu>
      </AppRuntimeProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run inline" }));
    expect(run).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Commands" }));
    const item = await screen.findByRole("menuitem", { name: "Run command" });
    expect(item.tagName).toBe("BUTTON");
    expect(item.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(item);
    expect(run).toHaveBeenCalledTimes(1);
  });

  test("keeps contributed dialogs mounted and restores focus to the toolbar trigger", async () => {
    render(<ActionMenu label="Connect" glyph="plus" variant="primary" size="sm"><FormAction /></ActionMenu>);
    const trigger = screen.getByRole("button", { name: "Connect" });
    fireEvent.click(trigger);
    const item = await screen.findByRole("menuitem", { name: "Connect example" });
    expect(item.getAttribute("aria-haspopup")).toBe("dialog");
    fireEvent.click(item);
    const dialog = await screen.findByRole("dialog", { name: "Example connection" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    expect(screen.getByRole("dialog", { name: "Example connection" })).toBe(dialog);
    expect(screen.getByRole("menuitem", { name: "Connect example", hidden: true })).toBe(item);
    expect(item.getAttribute("tabindex")).toBe("-1");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Connect example" }));
    expect(await screen.findByRole("dialog", { name: "Example connection" })).toBeTruthy();
  });

  test("supports keyboard navigation, dialog activation and Escape with toolbar focus restoration", async () => {
    render(<ActionMenu label="Connect">
      <ActionTrigger>Inspect example</ActionTrigger>
      <FormAction />
    </ActionMenu>);
    const trigger = screen.getByRole("button", { name: "Connect" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const first = await screen.findByRole("menuitem", { name: "Inspect example" });
    await waitFor(() => expect(document.activeElement).toBe(first));
    fireEvent.keyDown(first, { key: "ArrowDown" });
    const connect = screen.getByRole("menuitem", { name: "Connect example" });
    await waitFor(() => expect(document.activeElement).toBe(connect));
    fireEvent.keyDown(connect, { key: " " });
    const dialog = await screen.findByRole("dialog", { name: "Example connection" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  test("restores toolbar focus for a dialog opened after an asynchronous action", async () => {
    render(<ActionMenu label="Connect"><PairingAction /></ActionMenu>);
    const trigger = screen.getByRole("button", { name: "Connect" });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Pair example" }));
    expect(await screen.findByRole("dialog", { name: "Example pairing" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  test("closing a nested dialog returns to its trigger inside the still-open parent", async () => {
    render(<ActionMenu label="Connect"><NestedDialogAction /></ActionMenu>);
    const connect = screen.getByRole("button", { name: "Connect" });
    fireEvent.click(connect);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Connect example" }));
    const parent = await screen.findByRole("dialog", { name: "Example connection" });
    const create = within(parent).getByRole("button", { name: "Create credential" });
    fireEvent.click(create);
    const nested = await screen.findByRole("dialog", { name: "New credential" });
    await waitFor(() => expect(nested.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New credential" })).toBeNull());
    expect(screen.getByRole("dialog", { name: "Example connection" })).toBe(parent);
    await waitFor(() => expect(document.activeElement).toBe(create));
    fireEvent.keyDown(create, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(connect));
  });

  test("an ActionTrigger inside a menu-opened dialog is a tabbable button", async () => {
    const run = vi.fn();
    render(<ActionMenu label="Connect"><NestedDialogAction onInspect={run} /></ActionMenu>);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Connect example" }));
    const dialog = await screen.findByRole("dialog", { name: "Example connection" });
    const inspect = within(dialog).getByRole<HTMLButtonElement>("button", { name: "Inspect credential" });
    expect(inspect.type).toBe("button");
    expect(inspect.tabIndex).toBe(0);
    expect(within(dialog).queryByRole("menuitem", { hidden: true })).toBeNull();
    inspect.focus();
    expect(document.activeElement).toBe(inspect);
    fireEvent.click(inspect);
    expect(run).toHaveBeenCalledOnce();
  });

  test("aggregates pending contributions and clears loading on settling or unmounting", () => {
    const menu = (first: boolean, second: boolean, includeSecond = true) => (
      <React.StrictMode><ActionMenu label="Connect">
        <ActionTrigger loading={first}>First connection</ActionTrigger>
        {includeSecond ? <ActionTrigger loading={second}>Second connection</ActionTrigger> : null}
      </ActionMenu></React.StrictMode>
    );
    const { rerender } = render(menu(true, true));
    const trigger = screen.getByRole<HTMLButtonElement>("button", { name: "Connect" });
    expect(trigger.getAttribute("aria-busy")).toBe("true");
    expect(trigger.disabled).toBe(true);
    rerender(menu(false, true));
    expect(trigger.getAttribute("aria-busy")).toBe("true");
    rerender(menu(false, true, false));
    expect(trigger.getAttribute("aria-busy")).toBeNull();
    expect(trigger.disabled).toBe(false);
  });
});

function NestedDialogAction({ onInspect }: { onInspect?: () => void }): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  return (
    <DialogForm open={open} onOpenChange={setOpen} title="Example connection"
      trigger={<ActionTrigger>Connect example</ActionTrigger>}>
      <ActionTrigger onClick={onInspect}>Inspect credential</ActionTrigger>
      <DialogForm open={creating} onOpenChange={setCreating} title="New credential"
        trigger={<ActionTrigger>Create credential</ActionTrigger>}>
        <label htmlFor="credential-name">Credential name</label>
        <input id="credential-name" />
      </DialogForm>
    </DialogForm>
  );
}

function FormAction(): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return (
    <DialogForm open={open} onOpenChange={setOpen} title="Example connection"
      trigger={<ActionTrigger>Connect example</ActionTrigger>}>
      <label htmlFor="account">Account</label>
      <input id="account" />
    </DialogForm>
  );
}

function PairingAction(): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return <>
    <ActionTrigger onClick={async () => { await Promise.resolve(); setOpen(true); }}>Pair example</ActionTrigger>
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content>
          <Dialog.Title>Example pairing</Dialog.Title>
          <Dialog.Close>Done</Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>;
}
