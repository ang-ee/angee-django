// @vitest-environment happy-dom

import type { Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
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
import { ActionMenu, ActionTrigger } from "../../toolbars/ActionMenu";
import { createUiTestProviders } from "../../testing";
import { RecordChromeProvider } from "../resource/record-chrome-context";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import type { ActionDescriptor } from "../page";
import { jsonSchemaActionArgs } from "./json-schema";
import { useTrashActions } from "../resource/trash";

const { Provider, clearClients } = createUiTestProviders({
  queryClientConfig: { defaultOptions: {
    mutations: { retry: false }, queries: { retry: false },
  } },
});

describe("RecordActionBar", () => {
  test("primary alone puts a verb on the toolbar with primary emphasis", () => {
    renderActionBar(<RecordActionBar record={record} actions={[
      { id: "review", label: "Review", primary: true, run: vi.fn() },
    ]} />);
    expect(screen.getByRole("button", { name: "Review" }).classList.contains("bg-brand")).toBe(true);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test("explicit menu placement beats primary and lone-verb promotion", async () => {
    renderActionBar(<RecordActionBar record={record} fillBand actions={[
      { id: "review", label: "Review", placement: "menu", primary: true, run: vi.fn() },
    ]} />);
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Review" })).toBeTruthy();
  });

  test.each([null, { title: "Draft" }])("a record without a public id does not promote a bare run verb (%j)", async (draft) => {
    renderActionBar(<RecordActionBar record={draft} fillBand actions={[
      { id: "refresh", label: "Refresh skills", run: vi.fn() },
    ]} />);
    expect(screen.queryByRole("button", { name: "Refresh skills" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Refresh skills" })).toBeTruthy();
  });

  test.each<ActionDescriptor>([
    { id: "primary", label: "Review", primary: true, run: vi.fn() },
    { id: "toolbar", label: "Review", placement: "toolbar", run: vi.fn() },
  ])("create mode preserves $id toolbar placement", (action) => {
    renderActionBar(<RecordActionBar record={null} fillBand actions={[action]} />);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Review" }).disabled).toBe(false);
  });

  test.each<{
    name: string;
    promote: boolean;
    actions: ActionDescriptor[];
    toolbar: string[];
    menu: string[];
  }>([
    { name: "a lone unmarked verb stays in the menu by default", promote: false,
      actions: [{ id: "review", label: "Review" }], toolbar: [], menu: ["Review"] },
    { name: "an opted-in lone verb is a secondary button", promote: true,
      actions: [{ id: "review", label: "Review" }], toolbar: ["Review"], menu: [] },
    { name: "a lone danger verb stays in the menu", promote: true,
      actions: [{ id: "drop", label: "Drop", danger: true }], toolbar: [], menu: ["Drop"] },
    { name: "two unmarked non-danger verbs stay in the menu", promote: true,
      actions: [{ id: "review", label: "Review" }, { id: "inspect", label: "Inspect" }],
      toolbar: [], menu: ["Review", "Inspect"] },
    { name: "a non-danger verb beside a danger verb is promoted", promote: true,
      actions: [{ id: "review", label: "Review" }, { id: "drop", label: "Drop", danger: true }],
      toolbar: ["Review"], menu: ["Drop"] },
    { name: "an explicit toolbar verb prevents promotion", promote: true,
      actions: [{ id: "review", label: "Review" }, { id: "inspect", label: "Inspect", placement: "toolbar" }],
      toolbar: ["Inspect"], menu: ["Review"] },
    { name: "an explicitly placed menu verb does not count toward promotion", promote: true,
      actions: [{ id: "review", label: "Review" }, { id: "inspect", label: "Inspect", placement: "menu" }],
      toolbar: ["Review"], menu: ["Inspect"] },
  ])("$name", async ({ promote, actions, toolbar, menu }) => {
    renderActionBar(<RecordActionBar record={record} fillBand={promote} actions={actions} />);
    for (const name of toolbar) {
      const button = screen.getByRole("button", { name });
      expect(button.classList.contains("bg-inset")).toBe(true);
      expect(button.classList.contains("bg-brand")).toBe(false);
    }
    for (const name of menu) expect(screen.queryByRole("button", { name })).toBeNull();
    if (menu.length) {
      fireEvent.click(screen.getByRole("button", { name: "Actions" }));
      for (const name of menu) expect(await screen.findByRole("menuitem", { name })).toBeTruthy();
      for (const name of toolbar) expect(screen.queryByRole("menuitem", { name })).toBeNull();
    } else {
      expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    }
  });

  test("a primary verb prevents lone-verb promotion", async () => {
    renderActionBar(<RecordActionBar record={record} fillBand actions={[
      { id: "review", label: "Review", primary: true },
      { id: "inspect", label: "Inspect" },
    ]} />);
    expect(screen.getByRole("button", { name: "Review" }).classList.contains("bg-brand")).toBe(true);
    expect(screen.queryByRole("button", { name: "Inspect" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Inspect" })).toBeTruthy();
  });

  test.each(["visibleWhen", "permission"] as const)("%s filters verbs before promotion and toolbar detection", (gate) => {
    const hidden = gate === "visibleWhen" ? { visibleWhen: () => false } : { permission: "write" };
    renderActionBar(<RecordActionBar record={{ ...record, permissions: ["read"] }} fillBand actions={[
      { id: "review", label: "Review", permission: "read" },
      { id: "hidden", label: "Hidden", ...hidden },
      { id: "hidden-primary", label: "Hidden primary", primary: true, ...hidden },
      { id: "hidden-toolbar", label: "Hidden toolbar", placement: "toolbar", ...hidden },
    ]} />);
    expect(screen.getByRole("button", { name: "Review" }).classList.contains("bg-inset")).toBe(true);
    expect(screen.queryByRole("button", { name: /Hidden/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test.each<ActionDescriptor>([
    { id: "primary", label: "Review", primary: true },
    { id: "toolbar", label: "Review", placement: "toolbar" },
    { id: "lone", label: "Review" },
  ])("a parent Actions menu keeps the $id verb beside the generic group", async (action) => {
    renderActionBar(<ActionMenu><RecordActionBar record={record} fillBand actions={[action]}
      genericActions={[{ id: "archive", label: "Archive", placement: "menu", set: { archived: true } }]} />
    </ActionMenu>);
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Actions" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Review" })).toBeTruthy();
    const menu = within(screen.getByRole("menu"));
    expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Review", "Archive"]);
    expect(menu.getAllByRole("separator")).toHaveLength(1);
  });

  test("trash opens its native reason prompt without a second confirmation", async () => {
    const resource = testDataResource("test.Note", { resourceType: "test/note", fields: [{
      name: "is_trashed", kind: "scalar", scalar: "Boolean", readable: true, aggregatable: false,
      creatable: false, updatable: false, requiredOnCreate: false, trashable: true,
    }] });
    function TrashBar() {
      const actions = useTrashActions(resource);
      return <RecordActionBar record={{ id: "note_1", is_trashed: false, permissions: ["delete"] }} actions={actions} />;
    }
    renderActionBar(<TrashBar />);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Move to trash" }));
    const prompt = await screen.findByRole("alertdialog", { name: "Move this record to the trash?" });
    expect(within(prompt).getByLabelText("Reason (optional)")).toBeTruthy();
    expect(screen.queryByText("Are you sure?")).toBeNull();
    fireEvent.click(within(prompt).getByRole("button", { name: "Keep it" }));
  });

  test.each([false, true])("all verbs and Delete are native blocked menu buttons (parent menu: %s)", async (inParent) => {
    const run = vi.fn();
    const onDelete = vi.fn();
    const bar = <RecordActionBar record={record} blocked={!inParent}
      actions={[{ id: "inspect", label: "Inspect", run }]}
      genericActions={[{ id: "archive", label: "Archive", placement: "menu", set: { is_archived: true } }]}
      deleteAction={{ canDelete: true, isPending: false, onDelete }} />;
    renderActionBar(inParent ? <ActionMenu blocked>{bar}</ActionMenu> : bar);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    for (const name of ["Inspect", "Archive", "Delete"]) {
      const item = await screen.findByRole("menuitem", { name });
      expect(item.tagName).toBe("BUTTON");
      expect(item.getAttribute("aria-disabled")).toBe("true");
      fireEvent.click(item);
    }
    expect(run).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  test.each([false, true])("generic actions follow page and container verbs, with Delete last (parent menu: %s)", async (inParent) => {
    const bar = <RecordActionBar record={record}
      actions={[{ id: "inspect", label: "Inspect", placement: "menu", run: vi.fn() }]}
      contributedActions={<ActionTrigger>Export</ActionTrigger>}
      genericActions={[
        { id: "archive", label: "Archive", placement: "menu", set: { is_archived: true } },
        { id: "trash", label: "Move to trash", placement: "menu", run: vi.fn() },
      ]}
      deleteAction={{ canDelete: true, isPending: false, onDelete: vi.fn() }} />;
    renderActionBar(inParent ? <ActionMenu>{bar}</ActionMenu> : bar);
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    await screen.findByRole("menuitem", { name: "Archive" });
    const menu = within(screen.getByRole("menu"));
    expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Inspect", "Export", "Archive", "Move to trash", "Delete",
    ]);
    expect(menu.getAllByRole("separator")).toHaveLength(1);
  });

  test("Escape from a menu-originated args dialog restores focus to Actions", async () => {
    renderActionBar(<RecordActionBar record={record} actions={[
      { id: "review", label: "Review", args: [{ name: "reason", label: "Reason" }], submit: vi.fn() },
    ]} />);
    const trigger = screen.getByRole("button", { name: "Actions" });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Review" }));
    const dialog = await screen.findByRole("dialog", { name: "Review" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  test("omits verbs whose declared permission the record lacks", async () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={{ ...record, permissions: ["read"] }} actions={[
      { id: "edit", label: "Edit", permission: "write", placement: "toolbar", run },
      { id: "approve", label: "Approve", permission: "manage", run },
      { id: "inspect", label: "Inspect", permission: "read", run },
    ]} />);
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(screen.queryByRole("menuitem", { name: "Approve" })).toBeNull();
    expect(await screen.findByRole("menuitem", { name: "Inspect" })).toBeTruthy();
  });

  test("omits unavailable delete instead of disabling it", () => {
    renderActionBar(<RecordActionBar record={record} actions={[]}
      deleteAction={{ canDelete: false, isPending: false, onDelete: vi.fn() }} />);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });
  test("a contributed verb that renders nothing leaves no Actions menu", () => {
    // As a record with no startable workflow: the contribution decides at render time and offers nothing.
    renderActionBar(<RecordActionBar record={record} actions={[]} menuPlacement="leading" contributedActions={
      <RecordActionBar record={record} actions={[{ id: "hidden", label: "Hidden", run: vi.fn(), visibleWhen: () => false }]} />
    } />);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });
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
  test("menu actions expose their disabled reason and its recovery link", async () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={record} actions={[
      {
        id: "pay",
        label: "Register payment",
        run,
        disabledReason: <><span>Verify the destination first.</span> <a href="/verification">Review accounts</a></>,
      },
    ]} />);

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    const item = await screen.findByRole("menuitem", { name: "Register payment" });
    expect(item.getAttribute("aria-disabled")).toBe("true");
    expect(item.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByText("Verify the destination first.").parentElement?.id)
      .toBe(item.getAttribute("aria-describedby"));
    expect(screen.getByRole("link", { name: "Review accounts" }).getAttribute("href"))
      .toBe("/verification");
    fireEvent.click(item);
    expect(run).not.toHaveBeenCalled();
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
  test("a danger verb that runs on click asks the standard danger confirmation, titled by the verb", async () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={record} actions={[
      { id: "drop", label: "Drop", placement: "toolbar", danger: true, run },
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: "Drop" }));
    let confirmation = await screen.findByRole("alertdialog", { name: "Drop" });
    expect(within(confirmation).getByText("Are you sure?")).toBeTruthy();
    expect(toneOf(confirmation)).toBe("danger");
    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(run).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Drop" }));
    confirmation = await screen.findByRole("alertdialog", { name: "Drop" });
    fireEvent.click(within(confirmation).getByRole("button", { name: "Drop" }));
    await waitFor(() => expect(run).toHaveBeenCalledOnce());
  });
  test("a danger verb's own copy takes the danger tone; a danger form confirms in its form", async () => {
    const run = vi.fn();
    const submit = vi.fn(async () => ({ ok: true, message: "Removed." }));
    renderActionBar(<RecordActionBar record={record} actions={[
      { id: "reset", label: "Reset access", placement: "toolbar", danger: true, run,
        confirm: { title: "Reset requester access", body: "Reopen the decision?" } },
      { id: "remove", label: "Remove", placement: "toolbar", danger: true, args: [], submit },
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: "Reset access" }));
    const confirmation = await screen.findByRole("alertdialog", { name: "Reset requester access" });
    expect(toneOf(confirmation)).toBe("danger");
    fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("dialog", { name: "Remove" })).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(run).not.toHaveBeenCalled();
  });
  test("a submit verb without args runs through its action form, never a record patch", async () => {
    const applyPatch = vi.fn();
    const submit = vi.fn(async () => ({ ok: true, message: "Accepted." }));
    renderActionBar(<RecordActionBar record={record} applyPatch={applyPatch} reload={vi.fn()} actions={[
      { id: "accept", label: "Accept", placement: "toolbar", submit },
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    const dialog = await screen.findByRole("dialog", { name: "Accept" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({}, expect.objectContaining({ record, selectedIds: [record.id] })));
    expect(applyPatch).not.toHaveBeenCalled();
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

  test("only the first visible effective toolbar primary gets primary emphasis", async () => {
    const run = vi.fn();
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[
        { id: "menu-primary", label: "Menu primary", placement: "menu", primary: true, run },
        { id: "hidden", label: "Hidden", primary: true, run, visibleWhen: () => false },
        { id: "inspect", label: "Inspect", placement: "toolbar", run },
        { id: "review", label: "Review", primary: true, run },
        { id: "approve", label: "Approve", primary: true, run },
        { id: "archive", label: "Archive", run },
      ]} />);
    expect(screen.getByRole("button", { name: "Review" }).classList.contains("bg-brand")).toBe(true);
    for (const name of ["Inspect", "Approve"]) {
      expect(screen.getByRole("button", { name }).classList.contains("bg-inset")).toBe(true);
    }
    expect(screen.queryByRole("button", { name: "Hidden" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Menu primary" })).toBeTruthy();
    expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Review" })).toBeNull();
  });

  test("primary actions share confirmation, pending feedback and success settling", async () => {
    let finish!: (message: string) => void;
    const run = vi.fn(() => new Promise<string>((resolve) => { finish = resolve; }));
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[{ id: "cancel", label: "Cancel task", placement: "toolbar", primary: true, danger: true, run,
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
      blocked actions={[{ id: "review", label: "Review", placement: "toolbar", primary: true, run }]} />);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Review" }).disabled).toBe(true);
    cleanup();
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()}
      actions={[{ id: "review", label: "Review", placement: "toolbar", primary: true, disabled: true, run }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(run).not.toHaveBeenCalled();
  });

  test.each(["primary", "lone"] as const)("a %s action's typed-args dialog returns focus to its toolbar button", async (kind) => {
    const submit = vi.fn(async () => ({ ok: true, message: "Review recorded." }));
    renderActionBar(<RecordActionBar record={record} applyPatch={vi.fn()} reload={vi.fn()} fillBand={kind === "lone"}
      genericActions={[{ id: "archive", label: "Archive", placement: "menu", set: { is_archived: true } }]}
      actions={[{ id: "review", label: "Review", primary: kind === "primary", args: [], submit }]} />);
    const trigger = screen.getByRole("button", { name: "Review" });
    act(() => trigger.focus());
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Review" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({}, expect.objectContaining({ record, selectedIds: [record.id] })));
    expect(await screen.findByText("Review recorded.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Review" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
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
        <ActionTrigger ref={onTriggerRef} data-testid="credential-trigger">
          Update credential
        </ActionTrigger>
      }
    >
      <label htmlFor="username">Username</label>
      <input id="username" />
    </DialogForm>
  );
}

function toneOf(dialog: HTMLElement): string | null {
  return (dialog.closest("[data-tone]") ?? dialog.querySelector("[data-tone]"))?.getAttribute("data-tone") ?? null;
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
