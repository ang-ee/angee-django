// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { DialogForm } from "../fragments/DialogForm";
import { Dialog } from "../ui/dialog";
import { AppRuntimeProvider, createAngeeI18nInstance } from "../runtime";
import { ActionMenu, ActionTrigger } from "./ActionMenu";

afterEach(cleanup);

describe("ActionMenu", () => {
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
});

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
