// @vitest-environment happy-dom

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";

import { PaneRow, PaneRows } from "../communication/pane-list";
import { ModalsHost } from "../feedback";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { RadioGroupItem, RadioGroupRoot } from "../ui/radio-group";
import { Select } from "../ui/select";
import { Switch } from "../ui/switch";
import { DialogForm } from "./DialogForm";
import { ErrorBanner } from "./ErrorBanner";

function EditDialog({ children, description, footer = <button type="submit">Save</button> }: {
  children: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <DialogForm
      open={open}
      onOpenChange={setOpen}
      title="Edit"
      description={description}
      trigger={<button type="button">Open</button>}
      footer={footer}
    >
      {children}
    </DialogForm>
  );
}

/** Open the dialog with a pointer of `pointerType`, or from the keyboard when none is given, and wait for it. */
async function openDialog(pointerType?: "mouse" | "touch"): Promise<HTMLElement> {
  const trigger = screen.getByRole("button", { name: "Open" });
  if (pointerType) {
    fireEvent.pointerDown(trigger, { pointerType });
    fireEvent.click(trigger, { detail: 1 });
  } else {
    // A click with no pointer count is the one a key press on the button makes.
    fireEvent.click(trigger);
  }
  return screen.findByRole("dialog", { name: "Edit" });
}

describe("where focus is when a form dialog opens", () => {
  test.each([["a mouse", "mouse"], ["the keyboard", undefined]] as const)("opened by %s, the first field in its body has focus", async (_by, pointerType) => {
    render(<EditDialog>
      <p>Name the record.</p>
      <input aria-label="Name" />
      <input aria-label="Note" />
    </EditDialog>);
    await openDialog(pointerType);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Name" })));
  });

  test.each<[string, ReactNode]>([
    ["a read-only input", <input key="before" aria-label="Code" readOnly defaultValue="C-1" />],
    ["a disabled input", <input key="before" aria-label="Code" disabled />],
    ["a field disabled with a reason, which stays focusable", <span key="before" role="checkbox" aria-checked="false" aria-disabled="true" aria-label="Locked" tabIndex={0} />],
    ["a field taken out of the tab order", <input key="before" aria-label="Skipped" tabIndex={-1} />],
    ["an editing host marked read-only, with its own children", <div key="before" role="textbox" aria-label="Locked body" aria-readonly="true" contentEditable suppressContentEditableWarning><p>Line</p></div>],
    ["an editing host marked disabled, with its own children", <div key="before" role="textbox" aria-label="Locked body" aria-disabled="true" contentEditable suppressContentEditableWarning><p>Line</p></div>],
    ["a read-only switch", <span key="before" role="switch" aria-checked="false" aria-readonly="true" aria-label="Locked" tabIndex={0} />],
    ["a control in a hidden part", <div key="before" hidden><input aria-label="Hidden" /></div>],
    ["a control in an inert part", <div key="before" inert><input aria-label="Inert" /></div>],
    ["a control in a part that is not displayed", <div key="before" style={{ display: "none" }}><input aria-label="Undisplayed" /></div>],
    ["a field whose visibility is hidden", <input key="before" aria-label="Unseen" style={{ visibility: "hidden" }} />],
    ["a field in a part whose visibility is hidden", <div key="before" style={{ visibility: "hidden" }}><input aria-label="Unseen" /></div>],
    ["a link", <a key="before" href="/help">Help</a>],
    ["a plain button", <button key="before" type="button">Generate</button>],
  ])("%s before it is passed over: the next field a person can edit has focus", async (_case, before) => {
    render(<EditDialog>
      {before}
      <input aria-label="Name" />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Name" })));
  });

  test("an editable region first, as a text or code editor renders, has focus on its editing host", async () => {
    render(<EditDialog>
      <div role="textbox" aria-label="Body" aria-multiline="true" contentEditable suppressContentEditableWarning><p>Line</p></div>
      <input aria-label="Name" />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Body" })));
  });

  test("a native radio group first: its checked radio has focus, as Base UI's tabbable query picks it", async () => {
    render(<EditDialog>
      <fieldset>
        <legend>Size</legend>
        <input type="radio" name="size" value="s" aria-label="Small" />
        <input type="radio" name="size" value="m" aria-label="Medium" defaultChecked />
      </fieldset>
      <input aria-label="Name" />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Medium" })));
  });

  test("a native radio group whose checked radio is disabled: its first open radio has focus, since only an open field stands for the group", async () => {
    render(<EditDialog>
      <input type="radio" name="size" value="s" aria-label="Small" />
      <input type="radio" name="size" value="m" aria-label="Medium" defaultChecked disabled />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Small" })));
  });

  test("a checked radio of the same name in another form does not stand for the dialog's group: its first radio has focus", async () => {
    // A form inside the dialog's form is not valid markup, so React is not asked to render
    // one: only the radio's form owner matters, and the DOM gives it its nearest form.
    const elsewhere = (node: HTMLDivElement | null) => {
      if (!node || node.firstChild) return;
      const form = document.createElement("form");
      const radio = Object.assign(document.createElement("input"), { type: "radio", name: "size", value: "x", checked: true });
      radio.setAttribute("aria-label", "Elsewhere");
      form.append(radio);
      node.append(form);
    };
    render(<EditDialog>
      <input type="radio" name="size" value="s" aria-label="Small" />
      <input type="radio" name="size" value="m" aria-label="Medium" />
      <div ref={elsewhere} />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Small" })));
    expect(screen.getByRole<HTMLInputElement>("radio", { name: "Elsewhere" }).checked).toBe(true);
  });

  test("guard: a native radio group with no radio checked: its first radio has focus", async () => {
    render(<EditDialog>
      <input type="radio" name="size" value="s" aria-label="Small" />
      <input type="radio" name="size" value="m" aria-label="Medium" />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("radio", { name: "Small" })));
  });

  test.each<[string, { description?: ReactNode; footer?: ReactNode; body: ReactNode }, () => HTMLElement]>([
    ["the header, in its description, before a field in the body: the body's field", {
      description: <input aria-label="Header note" />, body: <input aria-label="Name" />,
    }, () => screen.getByRole("textbox", { name: "Name" })],
    ["the footer, under a body with no field: Close", {
      footer: <input aria-label="Footer note" />, body: <p>Nothing to fill in.</p>,
    }, () => screen.getByRole("button", { name: "Close" })],
  ])("a field in %s has focus, since only the body's fields are chosen", async (_case, parts, expected) => {
    render(<EditDialog description={parts.description} footer={parts.footer}>{parts.body}</EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(expected()));
  });

  test("where the platform has no checkVisibility, opening does not throw and the field has focus, by Base UI's own fallback", async () => {
    const checkVisibility = Element.prototype.checkVisibility;
    // A browser without it, such as Safari before 17.4.
    Reflect.deleteProperty(Element.prototype, "checkVisibility");
    try {
      render(<EditDialog><input aria-label="Name" /></EditDialog>);
      await openDialog();
      await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Name" })));
    } finally {
      Element.prototype.checkVisibility = checkVisibility;
    }
  });

  test("where the platform has no checkVisibility, a field that is not displayed is passed over, by the fallback's display test", async () => {
    const checkVisibility = Element.prototype.checkVisibility;
    Reflect.deleteProperty(Element.prototype, "checkVisibility");
    try {
      render(<EditDialog>
        <input aria-label="Undisplayed" style={{ display: "none" }} />
        <input aria-label="Name" />
      </EditDialog>);
      await openDialog();
      await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Name" })));
    } finally {
      Element.prototype.checkVisibility = checkVisibility;
    }
  });

  // Copies built from the same parts as the dialogs they name, whose own renders need more
  // than a form dialog's: what they hold before any field, and that holds no field.
  const retry = <Button size="sm" variant="secondary">Retry</Button>;
  test.each<[string, ReactNode]>([
    ["only buttons, under a banner with Retry", <>
      <ErrorBanner description="The preview failed." actions={retry} />
      <Button type="button" variant="secondary">Copy link</Button>
    </>],
    ["a list of verbs, as AddonSourceControls' scan list is", <PaneRows>
      <li><PaneRow icon="versions" title="Main" sub="main" verb={<Button variant="secondary" size="sm">Scan</Button>} /></li>
    </PaneRows>],
    ["rows under a banner with Retry, as AddonChangeDialog's preview is", <>
      <ErrorBanner description="The preview failed." actions={retry} />
      <PaneRows><li><PaneRow icon="list" title="Notes" sub="notes" /></li></PaneRows>
    </>],
    ["one record's people, one removable, as ManageAccessDialog's people view is", <section aria-label="People">
      <div>Ada <Button type="button" size="sm" variant="ghost">Remove access</Button></div>
    </section>],
    ["a selection whose access failed to load, as ManageAccessDialog's selection view is", <>
      <ErrorBanner description="Access could not be read." actions={retry} />
      <Select aria-label="Relation" value="viewer" disabled options={[{ value: "viewer", label: "viewer" }]} />
      <Button type="button" size="sm" variant="primary" disabled>Add</Button>
    </>],
  ])("a body of %s has no field: focus is where Base UI puts it, on Close, not on a button that acts", async (_case, body) => {
    render(<EditDialog>{body}</EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" })));
  });

  test("one record's people, one removable, then the field that adds a person, as ManageAccessDialog's people view is: that field has focus, not Remove access", async () => {
    render(<EditDialog>
      <section aria-label="People"><div>Ada <Button type="button" size="sm" variant="ghost">Remove access</Button></div></section>
      <section aria-label="Add a person">
        <Select aria-label="Relation" value="viewer" options={[{ value: "viewer", label: "viewer" }, { value: "editor", label: "editor" }]} />
      </section>
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Relation" })));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  test("guard: with no field a person can edit, only text, a read-only input and the footer's button, focus is where Base UI puts it: on Close", async () => {
    render(<EditDialog>
      <p>The record is locked.</p>
      <input aria-label="Code" readOnly defaultValue="C-1" />
    </EditDialog>);
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" })));
  });

  test("guard: opened by touch, the dialog itself has focus, as Base UI does for touch, so no on-screen keyboard rises over it", async () => {
    render(<EditDialog>
      <input aria-label="Name" />
    </EditDialog>);
    const dialog = await openDialog("touch");
    await waitFor(() => expect(document.activeElement).toBe(dialog));
  });

  test.each(["Escape", "Close"] as const)("guard: closed by %s, the dialog returns focus to its trigger", async (closeBy) => {
    render(<EditDialog>
      <input aria-label="Name" />
    </EditDialog>);
    const dialog = await openDialog();
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    if (closeBy === "Escape") fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    else fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Open" })));
  });
});

describe("a key already down when the dialog opens does nothing in it", () => {
  /** Dispatch a key event on `target`: whether its default survived, and whether it reached `target`. */
  function press(target: HTMLElement, type: "keyDown" | "keyUp", init: KeyboardEventInit): { kept: boolean; reached: boolean } {
    let reached = false;
    const seen = () => { reached = true; };
    const name = type === "keyDown" ? "keydown" : "keyup";
    target.addEventListener(name, seen);
    const kept = fireEvent[type](target, init);
    target.removeEventListener(name, seen);
    return { kept, reached };
  }
  const repeatedEnter = { key: "Enter", repeat: true };
  const stopped = { kept: false, reached: false };
  const passed = { kept: true, reached: true };

  /** Open a dialog holding `body` from the keyboard, unless `pointerType` says otherwise, and wait for focus in `field`. */
  async function openOn(body: ReactNode, field: () => HTMLElement, pointerType?: "mouse"): Promise<HTMLElement> {
    render(<EditDialog>{body}</EditDialog>);
    await openDialog(pointerType);
    await waitFor(() => expect(document.activeElement).toBe(field()));
    return field();
  }

  test("before any fresh key, a repeated Enter on a text input is stopped before the input sees it, its default prevented", async () => {
    const onKeyDown = vi.fn();
    const name = await openOn(<input aria-label="Name" onKeyDown={onKeyDown} />, () => screen.getByRole("textbox", { name: "Name" }));
    expect(press(name, "keyDown", repeatedEnter)).toEqual(stopped);
    expect(onKeyDown).not.toHaveBeenCalled();
  });

  test("before any fresh key, a repeated Enter on a switch is stopped: the switch stays off", async () => {
    const toggle = await openOn(<Switch aria-label="Notify" />, () => screen.getByRole("switch", { name: "Notify" }));
    expect(press(toggle, "keyDown", repeatedEnter)).toEqual(stopped);
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  test("before any fresh key, a repeated Enter on a select's trigger is stopped: nothing opens and nothing is chosen", async () => {
    const onValueChange = vi.fn();
    const trigger = await openOn(<Select aria-label="Size" options={[{ value: "s", label: "Small" }, { value: "m", label: "Medium" }]}
      onValueChange={onValueChange} />, () => screen.getByRole("combobox", { name: "Size" }));
    const presses: { kept: boolean; reached: boolean }[] = [];
    for (let repeat = 0; repeat < 3; repeat += 1) {
      // Enter on a native button whose keydown kept its default is a click in a browser;
      // the test environment makes none, so the test makes it.
      const result = press(document.activeElement as HTMLElement, "keyDown", repeatedEnter);
      if (result.kept && document.activeElement instanceof HTMLButtonElement) fireEvent.click(document.activeElement, { detail: 0 });
      presses.push(result);
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    }
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onValueChange).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
    expect(presses).toEqual([stopped, stopped, stopped]);
  });

  test.each<[string, ReactNode, () => HTMLElement]>([
    ["a switch", <Switch key="control" aria-label="Notify" />, () => screen.getByRole("switch", { name: "Notify" })],
    ["a checkbox", <Checkbox key="control" aria-label="Agree" />, () => screen.getByRole("checkbox", { name: "Agree" })],
    ["a radio", <RadioGroupRoot key="control" aria-label="Size"><RadioGroupItem value="s" label="Small" /></RadioGroupRoot>,
      () => screen.getByRole("radio", { name: "Small" })],
  ])("before any fresh key, the Space keyup that ends the opening press, on %s, is stopped: it stays off", async (_control, control, target) => {
    const field = await openOn(control, target);
    expect(press(field, "keyUp", { key: " " })).toEqual(stopped);
    expect(field.getAttribute("aria-checked")).toBe("false");
  });

  test.each<[string, ReactNode, () => HTMLElement, KeyboardEventInit]>([
    ["a repeated Enter in a textarea", <textarea key="control" aria-label="Note" />, () => screen.getByRole("textbox", { name: "Note" }), repeatedEnter],
    ["a repeated Backspace in an input", <input key="control" aria-label="Name" />, () => screen.getByRole("textbox", { name: "Name" }),
      { key: "Backspace", repeat: true }],
  ])("guard: after a fresh key, %s reaches it with its default", async (_case, control, target, repeated) => {
    const field = await openOn(control, target);
    expect(press(field, "keyDown", { key: "Backspace" })).toEqual(passed);
    expect(press(field, "keyDown", repeated)).toEqual(passed);
    expect(press(field, "keyUp", { key: "Backspace" })).toEqual(passed);
  });

  test("guard: after a fresh key, Space down and up on a switch flips it", async () => {
    const toggle = await openOn(<Switch aria-label="Notify" />, () => screen.getByRole("switch", { name: "Notify" }));
    press(toggle, "keyDown", { key: " " });
    expect(press(toggle, "keyUp", { key: " " }).reached).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  test("a second opening of the same dialog stops the opening key again", async () => {
    const toggle = () => screen.getByRole("switch", { name: "Notify" });
    await openOn(<Switch aria-label="Notify" />, toggle);
    expect(press(toggle(), "keyDown", { key: "a" })).toEqual(passed);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await openDialog();
    await waitFor(() => expect(document.activeElement).toBe(toggle()));
    expect(press(toggle(), "keyDown", repeatedEnter)).toEqual(stopped);
    expect(toggle().getAttribute("aria-checked")).toBe("false");
  });

  test("guard: a dialog opened by a mouse click and typed into at once: the first key is fresh and reaches its field", async () => {
    const name = await openOn(<input aria-label="Name" />, () => screen.getByRole("textbox", { name: "Name" }), "mouse");
    expect(press(name, "keyDown", { key: "a" })).toEqual(passed);
    expect(press(name, "keyUp", { key: "a" })).toEqual(passed);
  });
});

describe("where the primary pointer is coarse", () => {
  function OpenedByState() {
    const [open, setOpen] = useState(false);
    return <>
      <button type="button" onClick={() => setOpen(true)}>Open</button>
      <DialogForm open={open} onOpenChange={setOpen} title="Edit"><input aria-label="Name" /></DialogForm>
    </>;
  }

  test("a dialog opened through its open prop focuses itself, as Base UI does for touch, not its field", async () => {
    const matchMedia = window.matchMedia;
    window.matchMedia = (query: string) => ({
      matches: query === "(pointer: coarse)", media: query, onchange: null,
      addEventListener: () => undefined, removeEventListener: () => undefined,
    }) as unknown as MediaQueryList;
    try {
      render(<OpenedByState />);
      fireEvent.click(screen.getByRole("button", { name: "Open" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit" });
      await waitFor(() => expect(document.activeElement).toBe(dialog));
    } finally {
      window.matchMedia = matchMedia;
    }
  });

  test("guard: where the platform has no matchMedia, opening does not throw and the field has focus", async () => {
    const matchMedia = window.matchMedia;
    (window as { matchMedia?: Window["matchMedia"] }).matchMedia = undefined;
    try {
      render(<OpenedByState />);
      fireEvent.click(screen.getByRole("button", { name: "Open" }));
      await screen.findByRole("dialog", { name: "Edit" });
      await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Name" })));
    } finally {
      window.matchMedia = matchMedia;
    }
  });
});

describe("a dismissal", () => {
  /** A dialog whose open state its owner holds, with `pending` and `dirty` as given and a Cancel through `dismiss`. */
  function OwnedDialog({ pending = false, dirty = false }: { pending?: boolean; dirty?: boolean }) {
    const [open, setOpen] = useState(true);
    return <DialogForm open={open} onOpenChange={setOpen} title="Edit" pending={pending} dirty={dirty}
      footer={(dismiss) => <button type="button" onClick={dismiss}>Cancel</button>}>
      <input aria-label="Name" />
    </DialogForm>;
  }
  const renderOwned = (props: { pending?: boolean; dirty?: boolean }) => render(<ModalsHost><OwnedDialog {...props} /></ModalsHost>);
  const question = "Unsaved changes - leave without saving?";

  test.each(["Escape", "Close", "Cancel"] as const)("by %s is refused while a submission is pending", async (by) => {
    renderOwned({ pending: true });
    const dialog = await screen.findByRole("dialog", { name: "Edit" });
    if (by === "Escape") fireEvent.keyDown(dialog, { key: "Escape" });
    else fireEvent.click(screen.getByRole("button", { name: by }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.getByRole("dialog", { name: "Edit" })).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  test.each(["Escape", "Cancel"] as const)("by %s of a dirty form asks first: Stay keeps it open, Leave closes it", async (by) => {
    renderOwned({ dirty: true });
    const dismiss = async () => {
      const dialog = await screen.findByRole("dialog", { name: "Edit" });
      if (by === "Escape") fireEvent.keyDown(dialog, { key: "Escape" });
      else fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      return screen.findByRole("alertdialog", { name: question });
    };
    await dismiss();
    fireEvent.click(screen.getByRole("button", { name: "Stay" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(screen.getByRole("dialog", { name: "Edit" })).toBeTruthy();
    await dismiss();
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Edit" })).toBeNull());
  });

  test("of a clean form closes at once, asking nothing", async () => {
    renderOwned({});
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Edit" })).toBeNull());
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  test("guard: a close the owner makes itself through `open` is no dismissal: a dirty form closes without asking", async () => {
    const owned = (open: boolean) => <ModalsHost><DialogForm open={open} onOpenChange={() => undefined} title="Edit" dirty>
      <input aria-label="Name" />
    </DialogForm></ModalsHost>;
    const view = render(owned(true));
    await screen.findByRole("dialog", { name: "Edit" });
    view.rerender(owned(false));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Edit" })).toBeNull());
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
