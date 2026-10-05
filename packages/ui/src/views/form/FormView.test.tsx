// @vitest-environment happy-dom

import type {
  DataResourceMetadata,
  ModelMetadata,
  SchemaFieldMetadata,
} from "@angee/metadata";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
  } from "@testing-library/react";
import {
  Outlet,
  RouterContextProvider,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  } from "@tanstack/react-router";
import {
  AppRuntimeProvider,
  containersFromChildren,
  createRouteHref,
  type AppRuntime,
  type ContainerChild,
  type FormOverrideMap,
  } from "../../runtime";
import { modelLabelSegment } from "@angee/metadata";
import { testDataResource, withTestResourceInventory } from "@angee/metadata/testing";
import type { GetOneParams, NotificationProvider } from "@refinedev/core";
import type {
  Row,
} from "@angee/metadata";
import {
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { deserializeFormSpec } from "./form-spec";
import { Form } from "./Form";
import {
  FormView,
  FORM_CONTAINERS,
  type FormField,
  type FormSubmitContext,
} from "./FormView";
import { ActionTrigger } from "../../toolbars/ActionMenu";
import { useRecordChromeContext } from "../resource/record-chrome-context";
import {
  Action,
  Field,
  Group,
  Tab,
} from "../page";

const sdkMocks = {
  record: null as Row | null,
  listRows: [] as Row[],
  getOne: vi.fn<(params: GetOneParams) => Promise<{ data: Row }>>(),
  getList: vi.fn(),
  mutate: vi.fn(),
  recordSelection: undefined as readonly string[] | undefined,
  // When set, the record read answers with only the paths the view selected —
  // what a GraphQL detail query actually returns. Opt-in, so the tests that stub a
  // whole row and assert on it keep reading it whole; a test asserting that
  // FormView *selects* what it reads turns it on.
  projectToSelection: false,
};

type TestSchemaMetadata = Pick<SchemaFieldMetadata, "types"> &
  Partial<Pick<SchemaFieldMetadata, "labels" | "resources">>;

const statusOptions = [
  { value: "DRAFT", label: "Draft" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
];

const fields = [
  { name: "title", label: "Title", title: true },
  {
    name: "status",
    label: "Status",
    widget: "statusbar",
    status: true,
    options: statusOptions,
  },
  {
    name: "reminderAt",
    label: "Reminder",
    widget: "datetime",
  },
  {
    name: "createdAt",
    label: "Created At",
    widget: "datetime",
    readOnly: true,
  },
  { name: "wordCount", label: "Word Count", readOnly: true },
] satisfies readonly FormField[];

describe("FormView", () => {
  afterEach(() => {
    cleanup();
    clearClients();
  });

  beforeEach(() => {
    sdkMocks.record = {
      id: "note-1",
      title: "First",
      status: "ACTIVE",
      reminderAt: null,
      createdAt: "2026-05-31T12:00:00Z",
      wordCount: 3,
    };
    sdkMocks.mutate.mockReset();
    vi.mocked(dataProvider.update).mockClear();
    notificationProvider.open.mockClear();
    notificationProvider.close.mockClear();
    sdkMocks.listRows = [];
    sdkMocks.getOne.mockReset();
    sdkMocks.getList.mockReset();
    sdkMocks.recordSelection = undefined;
    sdkMocks.projectToSelection = false;
    sdkMocks.getOne.mockImplementation(async ({ meta }) => {
      const selection = fieldsFromMeta(meta);
      sdkMocks.recordSelection = selection;
      const record = sdkMocks.record;
      if (!record) throw new Error("No record configured for this detail request.");
      if (!sdkMocks.projectToSelection) return { data: record };
      const selected = new Set(selection.map((path) => path.split(".")[0]));
      return { data: Object.fromEntries(
        Object.entries(record).filter(([name]) => selected.has(name)),
      ) };
    });
    sdkMocks.getList.mockImplementation(async () => ({
      data: sdkMocks.listRows,
      total: sdkMocks.listRows.length,
    }));
    sdkMocks.mutate.mockImplementation(async ({ data }: { data: Row }) => ({
      ...sdkMocks.record,
      ...data,
    }));
  });

  test("throws when fields prop and field children are both declared", () => {
    expect(() =>
      renderWithProviders(
        <FormView resource="notes.Note" id="note-1" fields={fields}>
          <Field name="title" />
        </FormView>,
      ),
    ).toThrow(/cannot mix the fields\/groups props with element children/);
  });

  test("throws when groups prop and Group children are both declared", () => {
    expect(() =>
      renderWithProviders(
        <FormView
          resource="notes.Note"
          id="note-1"
          groups={[{ label: "Details", fields: [], actions: [] }]}
        >
          <Group label="Details">
            <Field name="title" />
          </Group>
        </FormView>,
      ),
    ).toThrow(/cannot mix the fields\/groups props with element children/);
  });

  test("an unknown model spelling degrades to a disabled form with a development warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    expect(() =>
      renderWithProviders(
        <FormView
          resource="missing.Note"
          id={null}
          fields={[{ name: "title", label: "Title" }]}
        />,
      ),
    ).not.toThrow();

    expect(
      (screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(warn).toHaveBeenCalledWith(
      expect.stringMatching(/model metadata lookup.*missing\.Note/),
    );
    warn.mockRestore();
  });

  test("renders declared record actions in the action menu", async () => {
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
      </FormView>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    await screen.findByRole("menuitem", { name: "Archive" });
  });

  test("a section contribution with a permission is absent for a reader without it", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read"] };
    const renderSections = () => renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, { types: { NoteType: {
      ...defaultModel("NoteType", "notes.Note"),
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
        reminderAt: { name: "reminderAt", kind: "scalar", scalar: "DateTime" },
      },
    } } }, undefined, formChildren({ "notes.Note#sections": {
      "notes.private": { permission: "write", content: <>
        <Group label="Private"><Field name="reminderAt" label="Reminder" /></Group>
        <Tab id="private-tab" label="Private tab">Private panel</Tab>
      </> },
    } }));
    renderSections();
    await screen.findByRole("heading", { name: "First" });
    expect(screen.queryByText("Private")).toBeNull();
    expect(screen.queryByRole("tab", { name: "Private tab" })).toBeNull();

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    renderSections();
    await screen.findByDisplayValue("First");
    expect(screen.getByText("Private")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Private tab" })).toBeTruthy();
  });

  test("Action declarations and record-verb children require their declared permission", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read"] };
    const renderActions = () => renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" title />
      <Action id="archive" label="Archive" permission="write" run={vi.fn()} />
    </FormView>, undefined, undefined, formChildren({ "notes.Note#actions": {
      "notes.reopen": { permission: "write", content: <button type="button">Reopen</button> },
    } }));
    renderActions();
    await screen.findByRole("heading", { name: "First" });
    expect(screen.queryByRole("button", { name: "Reopen" })).toBeNull();
    // The verb is a menu item: open the menu when there is one and look for it there.
    const actionsMenu = screen.queryByRole("button", { name: "Actions" });
    if (actionsMenu) fireEvent.click(actionsMenu);
    expect(screen.queryByRole("menuitem", { name: "Archive" })).toBeNull();

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    renderActions();
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
  });

  test("hides the record delete action when its predicate does not match", async () => {
    const deleteAction = {
      canDelete: true,
      isPending: false,
      onDelete: vi.fn(),
    };

    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        deleteAction={deleteAction}
        deleteVisibleWhen={(record) => record.status === "ARCHIVED"}
      >
        <Field name="title" label="Title" title />
        <Field name="status" label="Status" />
      </FormView>,
    );

    await screen.findByLabelText("Title");
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, status: "ARCHIVED" };
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        deleteAction={deleteAction}
        deleteVisibleWhen={(record) => record.status === "ARCHIVED"}
      >
        <Field name="title" label="Title" title />
        <Field name="status" label="Status" />
      </FormView>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Delete" })).toBeTruthy();
  });

  test("keeps every record-aware toolbar action operable", async () => {
    const publish = vi.fn();
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        toolbarStart={({ record }) =>
          record?.status === "ACTIVE" ? (
            <>
              <button type="button">Undo</button>
              <button type="button">Redo</button>
              <button type="button" onClick={publish}>Publish</button>
            </>
          ) : null
        }
      >
        <Field name="title" label="Title" title />
        <Field name="status" label="Status" />
      </FormView>,
    );

    const publishButton = await screen.findByRole("button", { name: "Publish" });
    fireEvent.click(publishButton);
    expect(publish).toHaveBeenCalledOnce();
  });


  test("runs a declarative set action through the update mutation", async () => {
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
      </FormView>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Archive" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({
        data: { id: "note-1", status: "ARCHIVED" },
      }),
    );
  });

  test("invokes a custom run action with the open-record context", async () => {
    const run = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="sync" label="Sync" run={run} />
      </FormView>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sync" }));

    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(run.mock.calls[0]?.[0]).toMatchObject({ record: { id: "note-1" } });
  });

  test("re-seeds when a native query refetch returns a new record for the same id", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} />);
    await screen.findByDisplayValue("First");

    sdkMocks.record = { ...sdkMocks.record, id: "note-1", title: "Refetched" };
    await act(async () => clients[0]!.refetchQueries({ type: "active" }));

    expect(await screen.findByDisplayValue("Refetched")).toBeTruthy();
  });

  test("keeps an existing-record form locked until its record loads", async () => {
    let resolveRecord!: (value: { data: Row }) => void;
    sdkMocks.getOne.mockImplementationOnce(() => new Promise((resolve) => { resolveRecord = resolve; }));
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        title={() => "Draft Document"}
        toolbar={<button type="button">Close record</button>}
        formExtras={() => <p>No readable documents attached</p>}
      />,
    );

    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByRole("button", { name: "Close record" })).toBeTruthy();
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
    expect(screen.queryByText("Draft Document")).toBeNull();
    expect(screen.queryByText("No readable documents attached")).toBeNull();

    await waitFor(() => expect(sdkMocks.getOne).toHaveBeenCalledOnce());
    await act(async () => resolveRecord({
      data: { id: "note-1", title: "Loaded", status: "ACTIVE" },
    }));

    expect(await screen.findByText("Draft Document")).toBeTruthy();
    expect(await screen.findByText("No readable documents attached")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Reminder" })).toBeTruthy();
  });

  test("renders standalone Form from Field and Group children", async () => {
    renderWithProviders(
      <Form resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Group label="Details">
          <Field name="wordCount" label="Word Count" readOnly />
        </Group>
      </Form>,
    );

    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );
    expect(screen.getByText("Details")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  test("renders labelled groups as tab panels when layout is tabs", async () => {
    renderWithProviders(
      <Form resource="notes.Note" id="note-1" layout="tabs">
        <Field name="title" label="Title" title />
        <Group label="Details">
          <Field name="summary" label="Summary" />
        </Group>
        <Group label="Schedule">
          <Field name="location" label="Location" />
        </Group>
      </Form>,
    );

    // Each labelled group becomes a tab; the title stays in the header.
    expect(await screen.findByRole("tab", { name: "Details" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Schedule" })).toBeTruthy();
    expect(await screen.findByLabelText("Title")).toBeTruthy();

    // The first tab's panel is shown; later panels mount only when selected.
    expect(screen.getByText("Summary")).toBeTruthy();
    expect(screen.queryByText("Location")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Schedule" }));
    expect(await screen.findByText("Location")).toBeTruthy();
    expect(screen.queryByText("Summary")).toBeNull();
  });

  test("keeps collapsible groups closed in the stacked body of a tabbed form", async () => {
    renderWithProviders(<Form resource="notes.Note" id="note-1" layout="tabs">
      <Field name="title" title />
      <Group label="Details" collapsible defaultOpen={false}><Field name="wordCount" /></Group>
      <Group label="Schedule"><Field name="reminderAt" /></Group>
    </Form>);
    expect(await screen.findByRole("tab", { name: "Schedule" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Details" })).toBeNull();
    const details = screen.getByRole("button", { name: "Details" });
    expect(details.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("true");
  });

  test.each(["document", "workspace"] as const)("%s forms share one strip for body and contributed record tabs", async (recordPresentation) => {
    const mounted = vi.fn();
    function RetainedPane({ active }: { active: boolean }): ReactElement {
      useEffect(() => { mounted(); }, []);
      return <output data-testid="body-retained-active">{String(active)}</output>;
    }
    renderWithProviders(<FormView resource="notes.Note" id="note-1" layout="tabs" recordPresentation={recordPresentation}
      bodyTabs={[{ id: "summary", label: "Summary", render: () => <p>Body summary</p> }]}
      recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <RetainedPane {...context} /> }]}>
      <Field name="title" title />
      <Group><Field name="wordCount" label="Word Count" /></Group>
      <Group label="Schedule"><Field name="reminderAt" label="Reminder" /></Group>
    </FormView>, undefined, undefined, formChildren({ "notes.Note#sections": {
      "notes.later": { sequence: 20, content: <Tab id="later" label="Later"><p>Later content</p></Tab> },
      "notes.earlier": { sequence: 10, content: <Tab id="earlier" label="Earlier" badge={2}><form aria-label="Contributed form" /></Tab> },
    } }));
    await screen.findByLabelText("Title");
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Summary", "Schedule", "Editor", "Earlier2", "Later"]);
    expect(screen.queryByRole("tab", { name: "Overview" })).toBeNull();
    expect(screen.getByText("Body summary")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /Earlier/ }));
    const contributedForm = await screen.findByRole("form", { name: "Contributed form" });
    expect(contributedForm.parentElement?.closest("form")).toBeNull();
    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByText("Word Count")).toBeTruthy();
    expect(screen.getByTestId("body-retained-active").textContent).toBe("false");
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    expect(screen.getByTestId("body-retained-active").textContent).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Schedule" }));
    expect(await screen.findByLabelText("Reminder")).toBeTruthy();
    expect(mounted).toHaveBeenCalledTimes(1);
  });

  test.each(["default", "controlled"])("%s tab selection opens a contributed id and reveal selects the field's body tab", async (selection) => {
    sdkMocks.record = { ...sdkMocks.record, location: "Office" };
    function Harness(): ReactElement {
      const [tab, setTab] = useState("pane");
      return <FormView resource="notes.Note" id="note-1" layout="tabs" defaultRecordTab={selection === "default" ? "pane" : undefined}
        recordTab={selection === "controlled" ? tab : undefined} onRecordTabChange={setTab}
        recordTabs={[{ id: "activity", label: "Activity", render: ({ focusField }) =>
          <button type="button" onClick={() => focusField("location", selection === "default" ? { recordTabId: "overview" } : undefined)}>Reveal location</button> }]}>
        <Field name="title" title />
        <Group label="Details"><Field name="wordCount" /></Group>
        <Group label="Schedule"><Field name="location" label="Location" /></Group>
      </FormView>;
    }
    renderWithProviders(<Harness />, undefined, undefined, formChildren({ "notes.Note#sections": {
      "notes.pane": { content: <Tab id="pane" label="Pane"><p>Contributed initial pane</p></Tab> },
    } }));
    await screen.findByText("Contributed initial pane");
    expect(screen.getByRole("tab", { name: "Pane" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    const reveal = await screen.findByRole("button", { name: "Reveal location" });
    fireEvent.click(reveal);
    const location = await screen.findByLabelText("Location");
    await waitFor(() => expect(document.activeElement).toBe(location));
    expect(screen.getByRole("tab", { name: "Schedule" }).getAttribute("aria-selected")).toBe("true");
  });

  test("create forms expose only body tabs and invalid defaults select their first tab", async () => {
    renderWithProviders(<FormView resource="notes.Note" layout="tabs" defaultRecordTab="missing"
      bodyTabs={[{ id: "summary", label: "Summary", render: () => <p>Body summary</p> }]}
      recordTabs={[{ id: "pane", label: "Pane", render: () => <p>Saved panel</p> }]}>
      <Field name="title" title />
    </FormView>, undefined, undefined, formChildren({ "notes.Note#sections": {
      "notes.pane": { content: <Tab id="contributed" label="Contributed"><p>Saved contribution</p></Tab> },
    } }));
    expect(await screen.findByText("Body summary")).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Summary"]);
    expect(screen.getByRole("tab", { name: "Summary" }).getAttribute("aria-selected")).toBe("true");
  });

  test("forms without body tabs retain the file preview's hidden Overview option", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} overviewTab={{ hidden: true }}
      recordTabs={[{ id: "preview", label: "Preview", render: () => <p>Record preview</p> }]} />);
    expect(await screen.findByText("Record preview")).toBeTruthy();
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Preview"]);
    expect(screen.queryByRole("tab", { name: "Overview" })).toBeNull();
  });

  test("rejects record ids colliding with body tab ids", () => {
    expect(() => renderWithProviders(<FormView resource="notes.Note" id="note-1" layout="tabs"
      bodyTabs={[{ id: "pane", label: "Body", render: () => null }]}
      recordTabs={[{ id: "pane", label: "Record", render: () => null }]} />)).toThrow(/duplicate record tab id "pane"/);
  });

  test("locks the saved form when projected permissions omit write", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "title", title: true }]} />);
    expect(await screen.findByRole("heading", { name: "First" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  });

  test("refuses an absent record without record chrome or fallback title", () => {
    renderWithProviders(<FormView resource="notes.Note" id="missing"
      fields={[{ name: "title", title: true }]}
      acknowledgedSource={{ record: null, values: null, loading: false }}
      actions={[{ id: "open", label: "Open", run: vi.fn() }]}
      recordTabs={[{ id: "activity", label: "Activity", render: () => "Activity panel" }]}
      contextLine={() => "Private context"} />);
    expect(screen.getByText("Record unavailable")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(screen.queryByText("Private context")).toBeNull();
    expect(screen.queryByText("Untitled")).toBeNull();
  });

  test("lets a long saved title wrap in the hero", async () => {
    sdkMocks.record = { ...sdkMocks.record, title: "A long record title with several words that need wrapping" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly fields={[{ name: "title", title: true }]} />);
    const heading = await screen.findByRole("heading", { name: String(sdkMocks.record.title) });
    expect(heading.className).toContain("break-words");
    expect(heading.className).not.toContain("truncate");
  });

  test("uses a wrapping editor for a writable title", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "title", title: true }]} />);
    const editor = await screen.findByRole("textbox", { name: "Title" }) as HTMLTextAreaElement;
    expect(editor.tagName).toBe("TEXTAREA");
    Object.defineProperty(editor, "scrollHeight", { configurable: true, get: () => editor.value.length * 2 });
    fireEvent.change(editor, { target: { value: "A long title that should grow as it wraps" } });
    expect(editor.style.height).toBe(`${editor.value.length * 2}px`);
    expect(editor.className).not.toContain("field-sizing");
  });

  test("renders rail-contributed fields through the form's editable widgets", async () => {
    const priority = { name: "priority", kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false };
    const resource = testDataResource("notes.Note", { fields: [priority] });
    const record = { id: "note-1", title: "First", priority: "High", permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      fields={[{ name: "title", title: true }]}
      recordTabs={[{ id: "activity", label: "Activity", keepMounted: true, render: () => <p>Recent activity</p> }]}
      acknowledgedSource={{ record, values: record }} />,
    { types: { NoteType: { resource, fields: { priority } } } }, undefined,
    formChildren({ "notes.Note#rail": { "notes.properties": {
      content: <FormView.RailGroup id="properties" label="Properties"
        fields={[{ field: { name: "priority" } }]} /> } } }));
    const rail = await screen.findByRole("complementary");
    expect(within(rail).getByText("Properties")).toBeTruthy();
    const input = within(rail).getByRole("textbox", { name: "Priority" });
    expect((input as HTMLInputElement).value).toBe("High");
    fireEvent.change(input, { target: { value: "Low" } });
    expect((input as HTMLInputElement).value).toBe("Low");
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByText("Recent activity")).toBeTruthy();
    expect(within(screen.getByRole("complementary")).getByText("Properties")).toBeTruthy();
    expect(document.querySelectorAll("aside")).toHaveLength(1);
  });

  test("a rail child's permission gates the groups it declares", async () => {
    const renderRail = (permissions: string[]) => {
      const record = { id: "note-1", title: "First", permissions };
      renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "title", title: true }]}
        acknowledgedSource={{ record, values: record }} />, undefined, undefined,
      formChildren({ "notes.Note#rail": {
        "notes.private": { permission: "manage",
          content: <FormView.RailGroup id="private" label="Private" content={<p>Private summary</p>} /> },
        "notes.public": { content: <FormView.RailGroup id="public" label="Public" content={<p>Public summary</p>} /> },
      } }));
    };
    renderRail(["read", "write"]);
    const rail = await screen.findByRole("complementary");
    expect(within(rail).getByText("Public summary")).toBeTruthy();
    expect(within(rail).queryByText("Private summary")).toBeNull();

    cleanup();
    renderRail(["read", "manage"]);
    expect(within(await screen.findByRole("complementary")).getByText("Private summary")).toBeTruthy();
  });

  test("derives a slug from the header title field while creating", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "slug", widget: "slug" },
        ]}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "Hello Brave World!" },
    });

    expect(
      (screen.getByRole("textbox", { name: "Slug" }) as HTMLInputElement).value,
    ).toBe("hello-brave-world");
  });

  test("does not overwrite a manually edited slug when the title changes", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "slug", widget: "slug" },
        ]}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "First Title" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Slug" }), {
      target: { value: "custom-slug" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "Second Title" },
    });

    expect(
      (screen.getByRole("textbox", { name: "Slug" }) as HTMLInputElement).value,
    ).toBe("custom-slug");
  });

  test("clears manual slug state when an edit form resets back to create", async () => {
    sdkMocks.record = null;

    function Harness(): ReactElement {
      const [id, setId] = useState<string | null>(null);
      return (
        <>
          <button
            type="button"
            onClick={() => {
              sdkMocks.record = {
                id: "note-1",
                title: "Saved Title",
                slug: "saved-title",
              };
              setId("note-1");
            }}
          >
            edit
          </button>
          <button
            type="button"
            onClick={() => {
              sdkMocks.record = null;
              setId(null);
            }}
          >
            create
          </button>
          <FormView
            resource="notes.Note"
            id={id}
            fields={[
              { name: "title", label: "Title", title: true },
              { name: "slug", widget: "slug" },
            ]}
          />
        </>
      );
    }

    renderWithProviders(<Harness />);
    fireEvent.change(screen.getByRole("textbox", { name: "Slug" }), {
      target: { value: "manual-slug" },
    });

    fireEvent.click(screen.getByRole("button", { name: "edit" }));
    await waitFor(() =>
      expect(
        (screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement).value,
      ).toBe("Saved Title"),
    );

    fireEvent.click(screen.getByRole("button", { name: "create" }));
    await waitFor(() =>
      expect(
        (screen.getByRole("textbox", { name: "Slug" }) as HTMLInputElement).value,
      ).toBe(""),
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "Fresh Title" },
    });
    expect(
      (screen.getByRole("textbox", { name: "Slug" }) as HTMLInputElement).value,
    ).toBe("fresh-title");
  });

  test("derives a slug from an explicit slugFrom source field", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "sourceName", label: "Source Name" },
          { name: "slug", widget: "slug", slugFrom: "sourceName" },
        ]}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), {
      target: { value: "Ignored Title" },
    });
    expect(
      (screen.getByRole("textbox", { name: "Slug" }) as HTMLInputElement).value,
    ).toBe("");

    fireEvent.change(screen.getByRole("textbox", { name: "Source Name" }), {
      target: { value: "Source Value" },
    });
    expect(
      (screen.getByRole("textbox", { name: "Slug" }) as HTMLInputElement).value,
    ).toBe("source-value");
  });

  test("submits only changed writable fields for an update", async () => {
    renderForm("note-1");

    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );

    fireEvent.change(title, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Renamed", id: "note-1" },
    });
    expect(await screen.findByText("Changes saved")).toBeTruthy();
    expect(dataProvider.update).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      resource: "notes",
      id: "note-1",
      variables: { title: "Renamed" },
    }));
    expect(notificationProvider.open).not.toHaveBeenCalled();
  });

  test("submits through a custom owner when the stock update root is absent", async () => {
    sdkMocks.record = { id: "provider-1", name: "Anthropic" };
    const resource = {
      ...defaultResource("InferenceProviderType", "agents.InferenceProvider"),
      roots: {
        ...defaultResource("InferenceProviderType", "agents.InferenceProvider").roots,
        update: null,
      },
      capabilities: ["list", "detail", "delete"],
    } satisfies DataResourceMetadata;
    const metadata: TestSchemaMetadata = {
      types: {
        InferenceProviderType: {
          ...defaultModel("InferenceProviderType", "agents.InferenceProvider"),
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
          },
          resource,
        },
      },
    };
    const submit = vi.fn(
      async (data: Record<string, unknown>, context: FormSubmitContext) => ({
        status: "ok" as const,
        data: { ...sdkMocks.record, ...data, id: context.id },
      }),
    );

    renderWithProviders(
      <FormView
        resource="agents.InferenceProvider"
        id="provider-1"
        fields={[{ name: "name", label: "Name", title: true }]}
        submit={submit}
      />,
      metadata,
    );

    const name = await screen.findByLabelText("Name");
    await waitFor(() =>
      expect((name as HTMLInputElement).value).toBe("Anthropic"),
    );
    fireEvent.change(name, { target: { value: "Claude" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith(
      { name: "Claude" },
      expect.objectContaining({
        id: "provider-1",
        isCreate: false,
        resource: "agents.InferenceProvider",
      }),
    );
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("hands a custom submit owner flat relation ids, not nested records", async () => {
    // The detail read carries the relation as a nested {id} record.
    sdkMocks.record = {
      id: "client-1",
      displayName: "Acme",
      reviewer: { id: "reviewer-1", displayName: "Reviewer One" },
    };
    const submit = vi.fn(
      async (data: Record<string, unknown>, context: FormSubmitContext) => ({
        status: "ok" as const,
        data: { ...sdkMocks.record, ...data, id: context.id },
      }),
    );
    const relationFields = [
      { name: "displayName", label: "Display Name", title: true },
      {
        name: "reviewer",
        label: "Reviewer",
        widget: "many2one",
        omittable: true,
        options: [
          { value: "reviewer-1", label: "Reviewer One" },
          { value: "reviewer-2", label: "Reviewer Two" },
        ],
      },
    ] satisfies readonly FormField[];

    renderWithProviders(
      <FormView
        resource="OAuthClient"
        id="client-1"
        fields={relationFields}
        submit={submit}
      />,
    );

    // The widget resolves the nested {id} record to the flat option id, so the
    // option label renders — proof the form holds "reviewer-1", not the object.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Reviewer/ }).textContent).toContain(
        "Reviewer One",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /Reviewer/ }));
    fireEvent.click(await screen.findByText("Reviewer Two"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Reviewer/ }).textContent).toContain(
        "Reviewer Two",
      ),
    );
    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "Acme Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    // The current scalar identity wins over the saved expanded relation.
    expect(submit).toHaveBeenCalledWith(
      { displayName: "Acme Renamed", reviewer: "reviewer-2" },
      expect.objectContaining({ id: "client-1", isCreate: false }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Not set" }));
    expect(screen.getByRole("button", { name: "Set value" })).toBeTruthy();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("renders fields read-only and does not offer save without an update root or custom submit", async () => {
    sdkMocks.record = { id: "provider-1", name: "Anthropic" };
    const resource = {
      ...defaultResource("InferenceProviderType", "agents.InferenceProvider"),
      roots: {
        ...defaultResource("InferenceProviderType", "agents.InferenceProvider").roots,
        update: null,
      },
      capabilities: ["list", "detail", "delete"],
    } satisfies DataResourceMetadata;
    const metadata: TestSchemaMetadata = {
      types: {
        InferenceProviderType: {
          ...defaultModel("InferenceProviderType", "agents.InferenceProvider"),
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
          },
          resource,
        },
      },
    };

    renderWithProviders(
      <FormView
        resource="agents.InferenceProvider"
        id="provider-1"
        fields={[{ name: "name", label: "Name", title: true }]}
      />,
      metadata,
    );

    expect(await screen.findByRole("heading", { name: "Anthropic" })).toBeTruthy();
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  test("does not require a mutation for a read-only detail form", async () => {
    sdkMocks.record = { id: "repo-1", name: "widgets", org: "acme" };
    renderWithProviders(
      <FormView
        resource="integrate_vcs.Repository"
        id="repo-1"
        fields={[
          { name: "org", label: "Org", readOnly: true },
          { name: "name", label: "Name", readOnly: true },
        ]}
      />,
    );

    expect(await screen.findByText("widgets")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(dataProvider.update).not.toHaveBeenCalled();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("includes an enum field when the user changes it", async () => {
    renderForm("note-1");

    await waitFor(() =>
      expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
        "First",
      ),
    );
    fireEvent.click(screen.getByText("Archived"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { status: "ARCHIVED", id: "note-1" },
    });
  });

  test("canonicalizes enum read values to option values before update submit", async () => {
    sdkMocks.record = {
      id: "note-1",
      title: "Anthropic",
      backend_class: "ANTHROPIC",
    };
    function Harness(): ReactElement {
      const [options, setOptions] = useState<typeof statusOptions>([]);
      return (
        <>
          <button
            type="button"
            onClick={() =>
              setOptions([
                { value: "anthropic", label: "Anthropic" },
                { value: "openai", label: "OpenAI" },
              ])
            }
          >
            load options
          </button>
          <FormView
            resource="notes.Note"
            id="note-1"
            fields={[
              { name: "title", label: "Title", title: true },
              {
                name: "backend_class",
                label: "Backend Class",
                widget: "select",
                options,
              },
            ]}
          />
        </>
      );
    }

    renderWithProviders(<Harness />);

    const backendClass = await screen.findByRole("combobox", {
      name: "Backend Class",
    });
    fireEvent.click(screen.getByRole("button", { name: "load options" }));
    await waitFor(() =>
      expect(backendClass.textContent).toContain("Anthropic"),
    );

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Claude" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Claude", id: "note-1" },
    });
  });

  test("folds the related label into the read and defers the option list to first open", async () => {
    // The detail read folds the related record's label (`reviewer.display_name`),
    // so the picker shows it with no option query. The list carries a DISTINCT
    // label, proving the read path uses the record's own label and never fetches
    // the 200-row option list until the picker is first opened.
    sdkMocks.record = {
      id: "provider-1",
      name: "Anthropic",
      reviewer: { id: "rev_1", display_name: "Primary Reviewer" },
    };
    sdkMocks.listRows = [{ id: "rev_1", display_name: "Reviewer From List" }];
    const metadata: TestSchemaMetadata = {
      types: {
        InferenceProviderType: {
          ...defaultModel("InferenceProviderType", "agents.InferenceProvider"),
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
            reviewer: {
              name: "reviewer",
              kind: "relation",
              relationModelLabel: "Reviewer",
            },
          },
          resource: {
            ...defaultResource("InferenceProviderType", "agents.InferenceProvider"),
            roots: {
              list: "inference_providers",
              detail: "inference_provider",
              update: "update_inference_provider",
            },
          },
        },
        ReviewerType: {
          ...defaultModel("ReviewerType", "Reviewer"),
          fields: {
            display_name: {
              name: "display_name",
              kind: "scalar",
              scalar: "String",
            },
          },
          resource: {
            ...defaultResource("ReviewerType", "Reviewer"),
            recordRepresentation: "display_name",
            roots: { list: "reviewers", detail: "reviewer" },
          },
        },
      },
    };

    renderWithProviders(
      <FormView
        resource="agents.InferenceProvider"
        id="provider-1"
        fields={[
          { name: "name", label: "Name", title: true },
          {
            name: "reviewer",
            label: "Reviewer",
            filters: [{ field: "company", operator: "eq", value: "cmp_1" }],
          },
        ]}
      />,
      metadata,
    );

    // The trigger label comes from the record read, and the deferred option
    // list has NOT fired on the editable-form mount (the headline guarantee).
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Reviewer: Primary Reviewer" }),
      ).toBeTruthy(),
    );
    expect(sdkMocks.getList).not.toHaveBeenCalled();
    expect(sdkMocks.recordSelection).toContain("reviewer");
    expect(sdkMocks.recordSelection).not.toContain("reviewer.id");
    expect(sdkMocks.recordSelection).not.toContain("reviewer.display_name");

    // Opening the picker fires the option list once; its fresh label then wins.
    fireEvent.click(
      screen.getByRole("button", { name: "Reviewer: Primary Reviewer" }),
    );
    await waitFor(() => expect(sdkMocks.getList).toHaveBeenCalledWith(
      expect.objectContaining({
        resource: "reviewers",
        filters: [{ field: "company", operator: "eq", value: "cmp_1" }],
      }),
    ));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Reviewer: Reviewer From List" }),
      ).toBeTruthy(),
    );
  });

  test("renders a relation title from its declared record representation", async () => {
    sdkMocks.record = {
      id: "run-1",
      workflow: { id: "workflow-1", name: "Daily briefing" },
    };
    renderWithProviders(
      <FormView
        resource="workflows.Run"
        id="run-1"
        fields={[{ name: "workflow", label: "Workflow", title: true, readOnly: true }]}
      />,
      workflowRelationMetadata(),
    );

    expect(await screen.findByRole("heading", { name: "Daily briefing" })).toBeTruthy();
    expect(screen.queryByText("[object Object]")).toBeNull();
  });

  test("read-only relation fields render retained references rather than picker controls", async () => {
    sdkMocks.record = { id: "run-1", workflow: { id: "workflow-1", name: "Daily briefing" } };
    renderWithProviders(<FormView resource="workflows.Run" id="run-1" readOnly
      fields={[{ name: "workflow", label: "Workflow" }]} />, workflowRelationMetadata(), undefined, {
      routeHref: createRouteHref([{ name: "workflows", path: "/workflows" }, { name: "workflow.record", path: "/workflows/$id" }]),
      routesByResource: { "workflows.Workflow": { collection: "workflows", record: { name: "workflow.record", param: "id" } } },
    });
    expect((await screen.findByRole("link", { name: "Daily briefing" })).getAttribute("href")).toBe("/workflows/workflow-1");
    expect(screen.queryByRole("button", { name: /Workflow/ })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(sdkMocks.getList).not.toHaveBeenCalled();
    expect(sdkMocks.getOne).toHaveBeenCalledOnce();
  });

  test("selects dotted scalar and relation fields from their owning model metadata", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "run-1", version: {
      id: "version-1", number: 2, workflow: { id: "workflow-1", name: "Daily briefing" },
    } };
    const metadata = workflowRelationMetadata();
    const run = metadata.types.RunType!;
    renderWithProviders(<FormView resource="workflows.Run" id="run-1" fields={[
      { name: "version.workflow", title: true }, { name: "version.number", label: "Version" },
    ]} />, { types: { ...metadata.types,
      RunType: { ...run, fields: { version: { name: "version", kind: "relation",
        relationObject: true, relationModelLabel: "workflows.Version" } } },
      VersionType: { ...defaultModel("VersionType", "workflows.Version"), fields: {
        number: { name: "number", kind: "scalar", scalar: "Int" },
        workflow: { ...run.fields.workflow!, relationObject: true },
      } },
    } });
    expect(await screen.findByRole("heading", { name: "Daily briefing" })).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining([
      "version.workflow.id", "version.workflow.name", "version.number",
    ]));
    expect(screen.queryByRole("textbox", { name: "Version" })).toBeNull();
  });

  test("selects a to-many relation's record identity and representation, not the bare object list", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "run-1", workflows: [{ id: "workflow-1", name: "Daily briefing" }] };
    const metadata = workflowRelationMetadata();
    const run = metadata.types.RunType!;
    renderWithProviders(<FormView resource="workflows.Run" id="run-1" fields={[
      { name: "workflows", hidden: true },
    ]} />, { types: { ...metadata.types,
      RunType: { ...run, fields: { ...run.fields,
        workflows: { name: "workflows", kind: "list", relationModelLabel: "workflows.Workflow" } } },
    } });
    await waitFor(() => expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining([
      "workflows.id", "workflows.name",
    ])));
    expect(sdkMocks.recordSelection).not.toContain("workflows");
  });

  test("a field naming its own leaf paths selects those, even when its list has no resource to represent it", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "run-1", stages: [{ step: { id: "step-1", name: "Collect" } }] };
    const metadata = workflowRelationMetadata();
    const run = metadata.types.RunType!;
    renderWithProviders(<FormView resource="workflows.Run" id="run-1" fields={[
      { name: "stages", hidden: true, selectionPaths: ["stages.step.id", "stages.step.name"] },
    ]} />, { types: { ...metadata.types,
      RunType: { ...run, fields: { ...run.fields,
        stages: { name: "stages", kind: "list", relationModelLabel: "workflows.RunStage" } } },
    } });
    await waitFor(() => expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining([
      "stages.step.id", "stages.step.name",
    ])));
    expect(sdkMocks.recordSelection).not.toContain("stages");
  });

  test("falls back from a missing relation label to identity, then Untitled", async () => {
    sdkMocks.record = { id: "run-1", workflow: { id: "workflow-1" } };
    const metadata = workflowRelationMetadata();

    renderWithProviders(
      <FormView
        resource="workflows.Run"
        id="run-1"
        fields={[{ name: "workflow", label: "Workflow", title: true, readOnly: true }]}
      />,
      metadata,
    );
    expect(await screen.findByRole("heading", { name: "workflow-1" })).toBeTruthy();

    cleanup();
    sdkMocks.record = { id: "run-2", workflow: null };
    renderWithProviders(
      <FormView
        resource="workflows.Run"
        id="run-2"
        fields={[{ name: "workflow", label: "Workflow", title: true, readOnly: true }]}
      />,
      metadata,
    );
    expect(await screen.findByRole("heading", { name: "Untitled" })).toBeTruthy();
    expect(screen.queryByText("[object Object]")).toBeNull();
  });

  test("renders an editable relation title through the native picker", async () => {
    sdkMocks.record = {
      id: "run-1",
      workflow: { id: "workflow-1", name: "Daily briefing" },
    };
    sdkMocks.listRows = [
      { id: "workflow-1", name: "Daily briefing" },
      { id: "workflow-2", name: "Weekly review" },
    ];

    renderWithProviders(
      <FormView
        resource="workflows.Run"
        id="run-1"
        fields={[{ name: "workflow", label: "Workflow", title: true }]}
      />,
      workflowRelationMetadata(),
    );

    const picker = await screen.findByRole("button", {
      name: "Workflow: Daily briefing",
    });
    expect(screen.queryByRole("textbox", { name: "Workflow" })).toBeNull();
    fireEvent.click(picker);
    fireEvent.click(await screen.findByText("Weekly review"));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Workflow: Weekly review" }),
      ).toBeTruthy(),
    );

    cleanup();
    sdkMocks.record = null;
    renderWithProviders(
      <FormView
        resource="workflows.Run"
        id={null}
        fields={[{ name: "workflow", label: "Workflow", title: true }]}
      />,
      workflowRelationMetadata(),
    );
    expect(screen.getByRole("button", { name: "Workflow" }).textContent).toContain(
      "Untitled",
    );
    expect(screen.queryByRole("textbox", { name: "Workflow" })).toBeNull();
  });

  test("submits create on title Enter while omitting blank non-string values", async () => {
    const metadata: TestSchemaMetadata = {
      types: {
        NoteType: {
          ...defaultModel("NoteType", "notes.Note"),
          fields: {
            title: { name: "title", kind: "scalar", scalar: "String" },
            note: { name: "note", kind: "scalar", scalar: "String" },
            priority: { name: "priority", kind: "enum" },
            assignee: {
              name: "assignee",
              kind: "scalar",
              scalar: "ID",
              relationModelLabel: "iam.User",
            },
            deadline: { name: "deadline", kind: "scalar", scalar: "DateTime" },
          },
        },
      },
    };
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "note", label: "Note" },
          {
            name: "priority",
            label: "Priority",
            widget: "select",
            options: [{ value: "high", label: "High" }],
          },
          {
            name: "assignee",
            label: "Assignee",
            widget: "many2one",
            options: [{ value: "user-1", label: "Ada" }],
          },
          { name: "deadline", label: "Deadline", widget: "datetime" },
        ]}
      />,
      metadata,
    );

    fireEvent.keyDown(screen.getByLabelText("Title"), { key: "Enter" });

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", note: "" },
    });
    expect(await screen.findByText("Record created")).toBeTruthy();
    expect(notificationProvider.open).not.toHaveBeenCalled();
  });

  test("omits blank numeric fields from create payloads", async () => {
    renderWithProviders(
      <FormView
        resource="agents.InferenceModel"
        fields={[
          { name: "name", label: "Name", title: true },
          { name: "contextWindow", label: "Context Window", widget: "integer" },
          { name: "maxOutputTokens", label: "Max Output Tokens", widget: "integer" },
          { name: "temperature", label: "Temperature", widget: "float" },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { name: "" },
    });
  });

  test("submits cleared numeric edits as null", async () => {
    sdkMocks.record = { id: "note-1", count: 4, ratio: 1.25 };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" fields={[
        { name: "count", label: "Count", widget: "integer" },
        { name: "ratio", label: "Ratio", widget: "float" },
      ]} />,
    );
    const count = await screen.findByLabelText("Count");
    const ratio = screen.getByLabelText("Ratio");
    fireEvent.change(count, { target: { value: "" } });
    fireEvent.blur(count);
    fireEvent.change(ratio, { target: { value: "" } });
    fireEvent.blur(ratio);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { id: "note-1", count: null, ratio: null },
    }));
  });

  test("merges default values into create payloads", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={fields}
        defaultValues={{ status: "ACTIVE" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", status: "ACTIVE" },
    });
  });

  test("submits a read-only field's defaultValue in the create payload", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "kind", label: "Kind", readOnly: true, defaultValue: "skill" },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    // The read-only field never renders an editable control, yet its create-seeded
    // default rides the payload (F-c) — the seed the page-level `createDefaults`
    // could only submit by faking the field editable.
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", kind: "skill" },
    });
  });

  test("submits a createOnly read-only field seeded via createDefaults", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "kind", label: "Kind", readOnly: true, createOnly: true },
        ]}
        defaultValues={{ kind: "skill" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    // The page-level `createDefaults` seed pins a read-only field with no field
    // `defaultValue`; it still rides the create payload instead of being dropped.
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", kind: "skill" },
    });
  });

  test("lets an explicit user edit override a field defaultValue on create", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "kind", label: "Kind", defaultValue: "skill" },
        ]}
      />,
    );

    fireEvent.change(await screen.findByLabelText("Kind"), {
      target: { value: "task" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    // Precedence: explicit user edit > defaultValue > empty value.
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", kind: "task" },
    });
  });

  test("ignores a field defaultValue on edit (create-only seed)", async () => {
    sdkMocks.record = { id: "note-1", title: "First", kind: "existing" };
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={[
          { name: "title", label: "Title", title: true },
          { name: "kind", label: "Kind", defaultValue: "skill" },
        ]}
      />,
    );

    const title = await screen.findByLabelText("Title");
    await waitFor(() => expect((title as HTMLInputElement).value).toBe("First"));
    // The editable field seeds from the record, never from the create default.
    await waitFor(() =>
      expect((screen.getByLabelText("Kind") as HTMLInputElement).value).toBe(
        "existing",
      ),
    );

    fireEvent.change(title, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Renamed", id: "note-1" },
    });
  });

  test("submits only fields accepted by the schema create input", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    const metadata: TestSchemaMetadata = {
      types: {
        IntegrationType: {
          ...defaultModel("IntegrationType", "integrate.Integration"),
          fields: {
            displayName: { name: "displayName", kind: "scalar", scalar: "String" },
            reviewer: { name: "reviewer", kind: "relation", relationModelLabel: "Reviewer" },
            owner: { name: "owner", kind: "relation", relationModelLabel: "iam.User" },
            credential: {
              name: "credential",
              kind: "relation",
              relationModelLabel: "Credential",
            },
            implClass: { name: "implClass", kind: "scalar", scalar: "String" },
            implLabel: { name: "implLabel", kind: "scalar", scalar: "String" },
            config: { name: "config", kind: "scalar", scalar: "JSON" },
            lastError: { name: "lastError", kind: "scalar", scalar: "String" },
          },
          resource: {
            ...defaultResource("IntegrationType", "integrate.Integration"),
            roots: {
              ...defaultResource("IntegrationType", "integrate.Integration").roots,
              create: "createIntegration",
            },
            createFields: ["reviewer", "owner", "credential", "implClass", "config"],
          },
        },
      },
    };
    const integrationFields = [
      { name: "displayName", label: "Display Name", title: true },
      { name: "reviewer", label: "Reviewer" },
      { name: "owner", label: "Owner" },
      { name: "credential", label: "Credential" },
      {
        name: "implClass",
        label: "Impl Class",
        prefill: () => ({
          displayName: "Github",
          implLabel: "GitHub",
          config: {},
        }),
      },
      { name: "implLabel", label: "Implementation" },
      { name: "config", label: "Config", widget: "json" },
      { name: "lastError", label: "Last Error", readOnly: true },
    ] satisfies readonly FormField[];

    renderWithProviders(
      <FormView
        resource="integrate.Integration"
        fields={integrationFields}
        defaultValues={{
          reviewer: "reviewer-1",
          owner: "user-1",
          credential: "credential-1",
        }}
      />,
      metadata,
    );

    fireEvent.change(screen.getByLabelText("Impl Class"), {
      target: { value: "github.vcs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: {
        reviewer: "reviewer-1",
        owner: "user-1",
        credential: "credential-1",
        implClass: "github.vcs",
        config: {},
      },
    });
  });

  test("overwrites pre-seeded sibling fields from impl prefill while editable", async () => {
    const implFields = [
      { name: "displayName", label: "Display Name", title: true },
      {
        name: "providerType",
        label: "Provider Type",
        prefill: (value) =>
          value === "oidc" ? { isEnabled: false, authorizeEndpoint: "/auth" } : null,
      },
      { name: "isEnabled", label: "Enabled", widget: "switch" },
      { name: "authorizeEndpoint", label: "Authorize Endpoint" },
    ] satisfies readonly FormField[];

    sdkMocks.record = null;
    renderWithProviders(
      <FormView
        resource="OAuthClient"
        fields={implFields}
        defaultValues={{ isEnabled: true }}
      />,
    );
    fireEvent.change(screen.getByLabelText("Provider Type"), {
      target: { value: "oidc" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: {
        displayName: "",
        providerType: "oidc",
        isEnabled: false,
        authorizeEndpoint: "/auth",
      },
    });

    cleanup();
    sdkMocks.record = {
      id: "client-1",
      displayName: "Client",
      providerType: "generic",
      isEnabled: true,
      authorizeEndpoint: "",
    };
    sdkMocks.mutate.mockClear();
    renderWithProviders(
      <FormView resource="OAuthClient" id="client-1" fields={implFields} />,
    );
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Display Name") as HTMLInputElement).value,
      ).toBe("Client"),
    );
    fireEvent.change(screen.getByLabelText("Provider Type"), {
      target: { value: "oidc" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: {
        providerType: "oidc",
        isEnabled: false,
        authorizeEndpoint: "/auth",
        id: "client-1",
      },
    });
  });

  test("implementation prefill preserves dirty common fields and replaces private config", async () => {
    renderWithProviders(
      <FormView
        resource="OAuthClient"
        fields={[
          { name: "displayName", label: "Display Name", title: true },
          {
            name: "providerType",
            label: "Provider Type",
            prefill: (value) => value === "second"
              ? { displayName: "Second preset", reviewer: "reviewer-2", privateConfig: "second-private" }
              : { displayName: "First preset", reviewer: "reviewer-1", privateConfig: "first-private" },
            prefillPreserveDirty: true,
            prefillReplace: ["privateConfig"],
          },
          { name: "reviewer", label: "Reviewer" },
          { name: "privateConfig", label: "Private Config" },
        ]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Provider Type"), { target: { value: "first" } });
    fireEvent.change(screen.getByLabelText("Display Name"), { target: { value: "My provider" } });
    fireEvent.change(screen.getByLabelText("Display Name"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Private Config"), { target: { value: "old" } });
    fireEvent.change(screen.getByLabelText("Provider Type"), { target: { value: "second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: {
      displayName: "",
      providerType: "second",
      reviewer: "reviewer-2",
      privateConfig: "second-private",
    } });
  });

  test("discard restores the original create baseline after implementation prefill", async () => {
    renderWithProviders(
      <FormView
        resource="OAuthClient"
        fields={[
          {
            name: "providerType",
            label: "Provider Type",
            prefill: () => ({ reviewer: "reviewer-2", privateConfig: "private" }),
            prefillPreserveDirty: true,
            prefillReplace: ["privateConfig"],
          },
          { name: "reviewer", label: "Reviewer" },
          { name: "privateConfig", label: "Private Config" },
        ]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Provider Type"), { target: { value: "second" } });
    expect((screen.getByLabelText("Reviewer") as HTMLInputElement).value).toBe("reviewer-2");
    expect((screen.getByLabelText("Private Config") as HTMLInputElement).value).toBe("private");

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect((screen.getByLabelText("Provider Type") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Reviewer") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Private Config") as HTMLInputElement).value).toBe("");
  });

  test("binds a declarative required error to a dotted field", async () => {
    sdkMocks.mutate.mockClear();
    renderWithProviders(
      <FormView resource="OAuthClient" fields={[
        { name: "config.local_root", label: "Local root", required: true },
      ]} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByLabelText("Local root").getAttribute("aria-required")).toBe("true");
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("renders structured presence controls and nested errors in the native form", async () => {
    const descriptors = deserializeFormSpec({
      type: "object",
      required: ["slots"],
      properties: {
        retry: { type: "object", widget: "object", omittable: true, properties: {
          max_attempts: { type: "integer", label: "Max attempts", defaultValue: 1, omittable: true },
        } },
        note: { type: "string", label: "Note", nullable: true, omittable: true },
        slots: { type: "array", widget: "list", label: "Slots", presenceRequired: true, minItems: 1, items: {
          type: "object", widget: "object", required: ["assignees"], properties: {
            assignees: { type: "array", widget: "list", label: "Assignees", presenceRequired: true, minItems: 1, items: { type: "string" } },
          },
        } },
      },
    }, defaultWidgets).map((field) => ({ ...field, name: `config.${field.name}` }));
    renderWithProviders(<FormView resource="OAuthClient" fields={descriptors} />);

    const retry = screen.getByText("Retry").closest('[data-layout="stack"]') as HTMLElement;
    expect(within(retry).getByText("Not set")).toBeTruthy();
    fireEvent.click(within(retry).getByRole("button", { name: "Set value" }));
    expect((await screen.findByLabelText("Max attempts") as HTMLInputElement).value).toBe("1");

    const note = screen.getByText("Note").closest('[data-layout="stack"]') as HTMLElement;
    fireEvent.click(within(note).getByRole("button", { name: "Leave empty" }));
    expect(within(note).getByText("Left empty")).toBeTruthy();

    const slots = screen.getByText("Slots").closest('[data-layout="stack"]') as HTMLElement;
    fireEvent.click(within(slots).getByRole("button", { name: "Add item" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const nestedError = await screen.findByText("This field is required.");
    expect(screen.getByText("Assignees").closest('[data-layout="stack"]')?.contains(nestedError)).toBe(true);
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("does not apply impl prefill on edit when the impl field is create-only", async () => {
    sdkMocks.record = {
      id: "client-1",
      displayName: "Client",
      providerType: "generic",
      isEnabled: true,
      authorizeEndpoint: "",
    };
    renderWithProviders(
      <FormView
        resource="OAuthClient"
        id="client-1"
        fields={[
          { name: "displayName", label: "Display Name", title: true },
          {
            name: "providerType",
            label: "Provider Type",
            createOnly: true,
            prefill: (value) =>
              value === "oidc" ? { isEnabled: false, authorizeEndpoint: "/auth" } : null,
          },
          { name: "isEnabled", label: "Enabled", widget: "switch" },
          { name: "authorizeEndpoint", label: "Authorize Endpoint" },
        ]}
      />,
    );

    await waitFor(() =>
      expect(
        (screen.getByLabelText("Display Name") as HTMLInputElement).value,
      ).toBe("Client"),
    );
    expect(screen.getByText("generic")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Authorize Endpoint"), {
      target: { value: "/manual" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { authorizeEndpoint: "/manual", id: "client-1" },
    });
  });

  test("submits fields declared through the groups prop", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        groups={[
          {
            label: "Details",
            actions: [],
            fields: [{ name: "title", label: "Title", title: true }],
          },
        ]}
      />,
    );

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Grouped" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Grouped" },
    });
  });

  test("provides the resource and record id to a record-chrome child", async () => {
    function ChromeProbe(): ReactElement {
      const chrome = useRecordChromeContext();
      return (
        <span data-testid="chrome-probe">
          {chrome.resource}:{chrome.recordId}
        </span>
      );
    }

    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      formChildren({ "form#chrome": { "notes.chrome": { content: <ChromeProbe /> } } }),
    );

    const probe = await screen.findByTestId("chrome-probe");
    expect(probe.textContent).toBe("notes.Note:note-1");
  });

  test("renders toolbar record verbs beside the Actions menu", async () => {
    function ActionProbe(): ReactElement {
      const chrome = useRecordChromeContext();
      return (
        <button type="button">
          Pause {chrome.recordId} via {chrome.dataProviderName} for{" "}
          {chrome.canonicalResource}
        </button>
      );
    }

    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
      </FormView>,
      undefined,
      undefined,
      formChildren({ "notes.Note#actions": { "notes.pause": { content: <ActionProbe /> } } }),
    );

    const actions = await screen.findByRole("button", { name: "Actions" });
    const pause = await screen.findByRole("button", {
      name: "Pause note-1 via console for notes.Note",
    });
    expect(pause.parentElement).toBe(actions.parentElement);
  });

  test("places #actions children in the toolbar and #actions-menu children in the Actions menu", async () => {
    // One verb component, two containers: the container decides the placement.
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      formChildren({
        "notes.Note#actions": { "notes.pause": { content: <ActionTrigger>Pause</ActionTrigger> } },
        "notes.Note#actions-menu": { "notes.export": { content: <ActionTrigger>Export</ActionTrigger> } },
      }),
    );

    const pause = await screen.findByRole("button", { name: "Pause" });
    const actions = screen.getByRole("button", { name: "Actions" });
    expect(pause.parentElement).toBe(actions.parentElement);
    expect(screen.queryByRole("button", { name: "Export" })).toBeNull();
    fireEvent.click(actions);
    expect(await screen.findByRole("menuitem", { name: "Export" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Pause" })).toBeNull();
  });

  test("shows the kind's, the canonical parent's and the concrete model's record verbs on a subtype form", async () => {
    // A verb declared once against the MTI parent reaches every subtype's form,
    // and a kind-level child reaches every form; all merge by sequence.
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      mtiMetadata(),
      undefined,
      formChildren({
        "form#actions": { "audit.history": { sequence: 30, content: <button type="button">History</button> } },
        "parties.Party#actions": { "parties.pause": { sequence: 20, content: <button type="button">Inherited pause</button> } },
        "notes.Note#actions": { "notes.publish": { sequence: 10, content: <button type="button">Publish</button> } },
      }),
    );

    await screen.findByRole("button", { name: "Inherited pause" });
    expect(buttonLabels(["History", "Inherited pause", "Publish"])).toEqual(["Publish", "Inherited pause", "History"]);
  });

  test("keeps a subtype's record verbs off its canonical parent's form", async () => {
    renderWithProviders(
      <FormView resource="parties.Party" id="party-1">
        <Field name="title" label="Title" title />
      </FormView>,
      mtiMetadata(),
      undefined,
      formChildren({
        "parties.Party#actions": { "parties.pause": { content: <button type="button">Generic pause</button> } },
        "notes.Note#actions": { "notes.publish": { content: <button type="button">Publish</button> } },
      }),
    );

    expect(await screen.findByRole("button", { name: "Generic pause" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
  });

  test.each([
    ["WHATSAPP", "WhatsApp disconnect", "Generic disconnect"],
    ["IMAP", "Generic disconnect", "WhatsApp disconnect"],
  ] as const)("a variant stands in for its original only on rows of its impl (%s)", async (kind, shown, absent) => {
    // The row reads its impl as the GraphQL enum member (`WHATSAPP`) while the
    // variant names the registry key (`whatsapp`); both land on one token.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "parties.Party#actions": {
          "parties.pause": { sequence: 11, content: <button type="button">Pause</button> },
          "parties.disconnect": { sequence: 13, content: <button type="button">Generic disconnect</button> },
        },
        "notes.Note#actions": {
          "whatsapp.disconnect": {
            variant: { of: "parties.disconnect", impl: "whatsapp" },
            sequence: 13,
            content: <button type="button">WhatsApp disconnect</button>,
          },
        },
      }),
    );

    expect(await screen.findByRole("button", { name: shown })).toBeTruthy();
    expect(screen.queryByRole("button", { name: absent })).toBeNull();
    // A verb the variant does not specialize stays on every row.
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  });

  test.each([
    ["WHATSAPP", ["WhatsApp pairing", "WhatsApp health", "Shared notes"], ["Generic connection", "IMAP folders"]],
    ["IMAP", ["Generic connection", "IMAP folders", "Shared notes"], ["WhatsApp pairing", "WhatsApp health"]],
  ] as const)("sections and the rail resolve per row: impl children and variants (%s)", async (kind, shown, absent) => {
    // Every candidate's fields are read; the row then admits its impl's children,
    // and a variant stands in for its original, as on the record verbs.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "notes.Note#sections": {
          "notes.connection": { content: <Group content={<p>Generic connection</p>} /> },
          "notes.shared": { content: <Group content={<p>Shared notes</p>} /> },
          "whatsapp.connection": {
            variant: { of: "notes.connection", impl: "whatsapp" },
            content: <Group content={<p>WhatsApp pairing</p>} />,
          },
          "imap.folders": { impl: "imap", content: <Group content={<p>IMAP folders</p>} /> },
          // A tab and an action from a row-decided child follow the row too.
          "notes.sync": { content: <Tab id="sync" label="Sync"><p>Generic sync</p></Tab> },
          "whatsapp.sync": {
            variant: { of: "notes.sync", impl: "whatsapp" },
            content: <Tab id="sync" label="WhatsApp sync"><p>WhatsApp sync</p></Tab>,
          },
          "imap.reindex": { impl: "imap", content: <Action id="reindex" label="Reindex IMAP" run={() => undefined} /> },
        },
        "notes.Note#rail": {
          "whatsapp.health": { impl: "whatsapp", content: <FormView.RailGroup id="health" label="Health" content={<p>WhatsApp health</p>} /> },
        },
      }),
    );

    for (const text of shown) expect(await screen.findByText(text)).toBeTruthy();
    for (const text of absent) expect(screen.queryByText(text)).toBeNull();
    // The original and its variant may reuse a tab id: only one reaches the row.
    expect(screen.getByRole("tab", { name: kind === "WHATSAPP" ? "WhatsApp sync" : "Sync" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: kind === "WHATSAPP" ? "Sync" : "WhatsApp sync" })).toBeNull();
    const actions = screen.queryByRole("button", { name: "Actions" });
    if (kind === "IMAP") {
      fireEvent.click(actions!);
      expect(await screen.findByRole("menuitem", { name: "Reindex IMAP" })).toBeTruthy();
    } else if (actions) {
      fireEvent.click(actions);
      await waitFor(() => expect(screen.queryByRole("menu")).toBeTruthy());
      expect(screen.queryByRole("menuitem", { name: "Reindex IMAP" })).toBeNull();
    }
  });

  test("a create form knows no implementation: originals stand, row-decided children stay out, fields and all", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata({ secret: { name: "secret", kind: "scalar", scalar: "String" } }),
      undefined,
      formChildren({
        "notes.Note#sections": {
          "notes.connection": { content: <Group content={<p>Generic connection</p>} /> },
          "whatsapp.connection": {
            variant: { of: "notes.connection", impl: "whatsapp" },
            content: <Group content={<p>WhatsApp pairing</p>} />,
          },
          // A required field of an impl child would otherwise block every create.
          "imap.secret": { impl: "imap", content: <Group label="IMAP"><Field name="secret" label="Secret" required /></Group> },
        },
      }),
    );

    expect(await screen.findByText("Generic connection")).toBeTruthy();
    expect(screen.queryByText("WhatsApp pairing")).toBeNull();
    expect(screen.queryByLabelText("Secret")).toBeNull();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "New" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { title: "New" } });
  });

  test("a group the record does not show lends the form no body", async () => {
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP", notes: "IMAP folder notes" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata({ notes: { name: "notes", kind: "scalar", scalar: "String" } }),
      undefined,
      formChildren({
        "notes.Note#sections": {
          "imap.notes": { impl: "imap", content: <Group label="IMAP"><Field name="notes" label="Notes" body /></Group> },
        },
      }),
    );

    await screen.findByLabelText("Title");
    expect(screen.queryByDisplayValue("IMAP folder notes")).toBeNull();
    expect(screen.queryByText("IMAP folder notes")).toBeNull();
  });

  test("a field an original and its variant both declare saves as the record's own declares it", async () => {
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP", label: "Old" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata({ label: { name: "label", kind: "scalar", scalar: "String" } }),
      undefined,
      formChildren({
        "notes.Note#sections": {
          "notes.label": { content: <Group label="Label"><Field name="label" label="Label" readOnly /></Group> },
          "whatsapp.label": {
            variant: { of: "notes.label", impl: "whatsapp" },
            content: <Group label="Label"><Field name="label" label="Label" /></Group>,
          },
        },
      }),
    );

    const label = await screen.findByLabelText("Label");
    await waitFor(() => expect((label as HTMLInputElement).value).toBe("Old"));
    fireEvent.change(label, { target: { value: "New" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { label: "New", id: "note-1" } });
  });

  test("a variant section or rail group the row may not see leaves its original (G-13)", async () => {
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP", permissions: ["read"] };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "notes.Note#sections": {
          "notes.connection": { content: <Group content={<p>Generic connection</p>} /> },
          "whatsapp.connection": {
            variant: { of: "notes.connection", impl: "whatsapp" },
            permission: "manage",
            content: <Group content={<p>WhatsApp pairing</p>} />,
          },
        },
        "notes.Note#rail": {
          "notes.health": { content: <FormView.RailGroup id="health" label="Health" content={<p>Generic health</p>} /> },
          "whatsapp.health": {
            variant: { of: "notes.health", impl: "whatsapp" },
            permission: "manage",
            content: <FormView.RailGroup id="health" label="Health" content={<p>WhatsApp health</p>} />,
          },
        },
      }),
    );

    expect(await screen.findByText("Generic connection")).toBeTruthy();
    expect(screen.queryByText("WhatsApp pairing")).toBeNull();
    expect(screen.getByText("Generic health")).toBeTruthy();
    expect(screen.queryByText("WhatsApp health")).toBeNull();
  });

  test("an impl child shows only on rows of its impl, beside another impl's child", async () => {
    // Two backends contribute a connect verb on one model as siblings; each row
    // resolves only its own, and neither displaces a model-level verb.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "IMAP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "notes.Note#actions": {
          "whatsapp.connect": { impl: "whatsapp", content: <button type="button">Pair WhatsApp</button> },
          "imap.connect": { impl: "imap", content: <button type="button">Connect IMAP</button> },
          "notes.pause": { content: <button type="button">Pause</button> },
        },
      }),
    );

    expect(await screen.findByRole("button", { name: "Connect IMAP" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pair WhatsApp" })).toBeNull();
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  });

  test("selects the impl column it resolves variants and impl children from", async () => {
    // The regression this guards: the row's impl is read from
    // `displayRecord[implField]`, but the selection is built from the form's
    // *declared* fields — and this form declares only `title`. `_impl_fields`
    // intersects with the **resource's** readable columns, which is not the
    // form's selection, so nothing made the two meet. WhatsApp's verbs rendered
    // only because messaging's channel form happened to carry a presentational
    // `<Field name="backend_class" readOnly />`; deleting that line took every
    // WhatsApp verb with it — no error, no failing test. So the read is driven
    // here: the row answers with what the view selected and nothing more.
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "note-1", title: "Note", kind: "WHATSAPP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "notes.Note#actions": {
          "whatsapp.connect": { impl: "whatsapp", content: <button type="button">Pair WhatsApp</button> },
        },
      }),
    );

    expect(await screen.findByRole("button", { name: "Pair WhatsApp" })).toBeTruthy();
    expect(sdkMocks.recordSelection).toContain("kind");
  });

  test("selects no impl column the resource does not name", async () => {
    // The control for the test above. Same form, same row, same child — only
    // `implFields` is gone from the resource, so the view has no column to
    // select, the row arrives without `kind`, and the impl never resolves.
    // That is what proves the verb above rendered because the *view selected* the
    // column, rather than because the harness handed it a whole stubbed row.
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "note-1", title: "Note", kind: "WHATSAPP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      mtiMetadata(),
      undefined,
      formChildren({
        "notes.Note#actions": {
          "whatsapp.connect": { impl: "whatsapp", content: <button type="button">Pair WhatsApp</button> },
        },
      }),
    );

    await screen.findByDisplayValue("Note");
    expect(sdkMocks.recordSelection).not.toContain("kind");
    expect(screen.queryByRole("button", { name: "Pair WhatsApp" })).toBeNull();
  });

  test("checks record-verb permission against the row in the toolbar and the menu, variants included", async () => {
    const renderVerbs = () => renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "parties.Party#actions": {
          "parties.pause": { permission: "write", content: <ActionTrigger>Pause</ActionTrigger> },
        },
        "parties.Party#actions-menu": {
          "parties.disconnect": { permission: "write", content: <ActionTrigger>Generic disconnect</ActionTrigger> },
        },
        "notes.Note#actions-menu": {
          "whatsapp.disconnect": {
            variant: { of: "parties.disconnect", impl: "whatsapp" },
            permission: "write",
            content: <ActionTrigger>WhatsApp disconnect</ActionTrigger>,
          },
        },
      }),
    );
    sdkMocks.record = { ...sdkMocks.record, kind: "WHATSAPP", permissions: ["read"] };
    renderVerbs();
    await screen.findByRole("heading", { name: "First" });
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    // Nothing is left for the overflow menu, so there is none.
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    renderVerbs();
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "WhatsApp disconnect" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Generic disconnect" })).toBeNull();
  });

  test("orders record verbs by sequence across model and impl children", async () => {
    // `sequence` stays the ordering contract across the merged addresses: a
    // backend's Connect(10) on the concrete model lands before the inherited
    // Pause(11), not after every canonical verb.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      formChildren({
        "parties.Party#actions": {
          "parties.pause": { sequence: 11, content: <button type="button">Pause</button> },
          "parties.disconnect": { sequence: 13, content: <button type="button">Disconnect</button> },
        },
        "notes.Note#actions": {
          "whatsapp.connect": { impl: "whatsapp", sequence: 10, content: <button type="button">Connect</button> },
        },
      }),
    );

    await screen.findByRole("button", { name: "Connect" });
    expect(buttonLabels(["Connect", "Pause", "Disconnect"])).toEqual(["Connect", "Pause", "Disconnect"]);
  });

  test("two variants of one verb for the row's impl throw; a row of another impl renders the original", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const renderRow = (kind: string) => {
      const record = { id: "note-1", title: "First", kind };
      renderWithProviders(
        <FormView resource="notes.Note" id="note-1" acknowledgedSource={{ record, values: record }}>
          <Field name="title" label="Title" title />
        </FormView>,
        implMetadata(),
        undefined,
        formChildren({
          "parties.Party#actions": {
            "parties.disconnect": { content: <button type="button">Generic disconnect</button> },
          },
          "notes.Note#actions": {
            "whatsapp.disconnect": {
              variant: { of: "parties.disconnect", impl: "whatsapp" },
              content: <button type="button">WhatsApp disconnect</button>,
            },
            "chatbridge.disconnect": {
              variant: { of: "parties.disconnect", impl: "whatsapp" },
              content: <button type="button">Bridge disconnect</button>,
            },
          },
        }),
      );
    };
    try {
      expect(() => renderRow("WHATSAPP")).toThrow(
        // Siblings resolve in id order, so the clash names them that way.
        /"chatbridge.disconnect" and "whatsapp.disconnect" of "form#actions" are both variants of "parties.disconnect" for this row/,
      );
    } finally {
      consoleError.mockRestore();
    }

    cleanup();
    renderRow("IMAP");
    expect(await screen.findByRole("button", { name: "Generic disconnect" })).toBeTruthy();
  });

  test("merges #sections group fields into the submit payload", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      formChildren({
        "notes.Note#sections": {
          "notes.extra": {
            content: (
              <Group label="Extra">
                <Field name="extraCode" label="Extra Code" />
              </Group>
            ),
          },
        },
      }),
    );

    expect(screen.getByText("Extra")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Extra Title" },
    });
    fireEvent.change(screen.getByLabelText("Extra Code"), {
      target: { value: "extra-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Extra Title", extraCode: "extra-1" },
    });
  });

  test("uses every model contribution without a page admission prop", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, { types: { NoteType: {
      ...defaultModel("NoteType", "notes.Note"),
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
        reminderAt: { name: "reminderAt", kind: "scalar", scalar: "DateTime" },
      },
    } } }, undefined, formChildren({ "notes.Note#sections": {
      "notes.extra": { content: <Group label="Extra"><Field name="reminderAt" label="Reminder" /></Group> },
    } }));
    await screen.findByDisplayValue("First");
    expect(screen.getByText("Extra")).toBeTruthy();
    expect(sdkMocks.recordSelection).toContain("reminderAt");
  });

  test("places the declared status field before the hero and the lead body before secondary fields", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field name="status" label="Status" widget="statusbar" status options={statusOptions} />
      <Group label="Details"><Field name="wordCount" label="Words" /></Group>
      <Field name="body" label="Lead body" body />
    </FormView>);
    const title = await screen.findByDisplayValue("First");
    const status = screen.getByRole("list");
    expect(status.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByLabelText("Status")).toBeNull();
    expect(screen.getByText("Lead body").compareDocumentPosition(screen.getByText("Details")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test.each([
    ["parties.Party", "Ready", true],
    ["notes.Note", "Ready", true],
    ["notes.Note", "Empty", false],
  ] as const)("selects inherited tab dependencies on %s and evaluates %s visibility", async (resource, title, visible) => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "record-1", title };
    const visibleWhen = vi.fn((record: Row) => record.title === "Ready");
    renderWithProviders(
      <FormView resource={resource} id="record-1" fields={[]} />,
      mtiMetadata(),
      undefined,
      formChildren({
        "parties.Party#sections": {
          "parties.related": {
            content: (
              <Tab id="related" label="Related" requiredFields={["title"]} visibleWhen={visibleWhen}>
                <p>Related records</p>
              </Tab>
            ),
          },
        },
      }),
    );
    await waitFor(() => expect(visibleWhen).toHaveBeenCalledWith(expect.objectContaining({ title })));
    expect(sdkMocks.recordSelection).toContain("title");
    expect(screen.queryAllByRole("tab", { name: "Related" })).toHaveLength(visible ? 1 : 0);
  });

  test("contributed #sections keep their container order, before/after anchors included", async () => {
    // The kind-level and model-level children position as one list; an anchored
    // child sits beside its anchor rather than sorting last for lacking a sequence.
    sdkMocks.record = { id: "note-1", title: "Ordered note" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      formChildren({
        "form#sections": {
          "audit.trail": { sequence: 30, content: <Tab id="audit" label="Audit"><p>Audit trail</p></Tab> },
        },
        "notes.Note#sections": {
          "notes.extra": { sequence: 10, content: <Tab id="extra" label="Extra"><p>Extra</p></Tab> },
          "notes.before-audit": { before: "audit.trail", content: <Tab id="before" label="Before audit"><p>Before</p></Tab> },
          "notes.after-extra": { after: "notes.extra", content: <Tab id="after" label="After extra"><p>After</p></Tab> },
        },
      }),
    );

    await screen.findByRole("tab", { name: "Audit" });
    const contributed = screen.getAllByRole("tab").map((tab) => tab.textContent)
      .filter((label) => ["Extra", "After extra", "Before audit", "Audit"].includes(label ?? ""));
    expect(contributed).toEqual(["Extra", "After extra", "Before audit", "Audit"]);
  });

  test("projects the requiredFields of every admitted child, impl children and variants included, whatever the row", async () => {
    // The row's impl is unknown until it loads, so the read selects what any
    // impl's children and any variant need; permissions do not narrow it either.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "IMAP", permissions: [] };
    const scalar = (name: string) => ({ name, kind: "scalar" as const, scalar: "String" });
    const note = mtiModel("NoteType", "notes.Note", "parties.Party", ["kind"]);
    const metadata = withTestResourceInventory({ types: {
      NoteType: { ...note, fields: { ...note.fields, ...Object.fromEntries(
        ["kind", "pairedAt", "bridgeId", "menuFlag", "chromeFlag", "sectionFlag"].map((name) => [name, scalar(name)]),
      ) } },
      PartyType: mtiModel("PartyType", "parties.Party", "parties.Party"),
    } });
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      metadata,
      undefined,
      formChildren({
        "parties.Party#actions": {
          "parties.disconnect": { content: <button type="button">Generic disconnect</button> },
        },
        "notes.Note#actions": {
          "whatsapp.connect": { impl: "whatsapp", requiredFields: ["pairedAt"], content: <button type="button">Pair WhatsApp</button> },
          "whatsapp.disconnect": {
            variant: { of: "parties.disconnect", impl: "whatsapp" },
            requiredFields: ["bridgeId"],
            content: <button type="button">WhatsApp disconnect</button>,
          },
        },
        "notes.Note#actions-menu": {
          "whatsapp.repair": { impl: "whatsapp", requiredFields: ["menuFlag"], content: <button type="button">Repair</button> },
        },
        "form#chrome": {
          "audit.badge": { permission: "manage", requiredFields: ["chromeFlag"], content: <span>Audit badge</span> },
        },
        "notes.Note#sections": {
          "notes.extra": { requiredFields: ["sectionFlag"], content: <Tab id="extra" label="Extra"><p>Extra</p></Tab> },
        },
      }),
    );

    expect(await screen.findByRole("button", { name: "Generic disconnect" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pair WhatsApp" })).toBeNull();
    expect(screen.queryByRole("button", { name: "WhatsApp disconnect" })).toBeNull();
    expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining(["kind", "pairedAt", "bridgeId", "menuFlag", "chromeFlag", "sectionFlag"]));
  });

  test("composes #sections tabs through the saved-record tab owner", async () => {
    sdkMocks.record = { id: "note-1", title: "Contributed note" };

    function ContributedRecordPane(): ReactElement {
      const context = useRecordChromeContext();
      return <p>Pane for {context.recordId}</p>;
    }

    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      formChildren({
        "notes.Note#sections": {
          "notes.pane": {
            content: (
              <Tab id="pane" label="Pane" badge={7}>
                <ContributedRecordPane />
              </Tab>
            ),
          },
        },
      }),
    );

    const paneTab = await screen.findByRole("tab", { name: /Pane/ });
    expect(screen.getAllByRole("tablist")).toHaveLength(1);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Overview", "Pane7"]);
    expect(paneTab.textContent).toContain("7");
    fireEvent.click(paneTab);
    expect(await screen.findByText("Pane for note-1")).toBeTruthy();
  });

  test("rejects a contributed record tab that claims the reserved overview id", async () => {
    sdkMocks.record = { id: "note-1", title: "Contributed note" };
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() =>
        renderWithProviders(
          <FormView resource="notes.Note" id="note-1">
            <Field name="title" label="Title" title />
          </FormView>,
          undefined,
          undefined,
          formChildren({
            "notes.Note#sections": {
              "notes.overview": {
                content: (
                  <Tab id="overview" label="Shadow overview">
                    <p>contributed</p>
                  </Tab>
                ),
              },
            },
          }),
        ),
      ).toThrow(/duplicate record tab id "overview"/);
    } finally {
      consoleError.mockRestore();
    }
  });

  test("rejects a contributed record tab whose id collides with a declared one", async () => {
    sdkMocks.record = { id: "note-1", title: "Contributed note" };
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() =>
        renderWithProviders(
          <FormView
            resource="notes.Note"
            id="note-1"
            recordTabs={[
              { id: "pane", label: "Declared", render: () => <p>declared</p> },
            ]}
          >
            <Field name="title" label="Title" title />
          </FormView>,
          undefined,
          undefined,
          formChildren({
            "notes.Note#sections": {
              "notes.pane": {
                content: (
                  <Tab id="pane" label="Pane">
                    <p>contributed</p>
                  </Tab>
                ),
              },
            },
          }),
        ),
      ).toThrow(/duplicate record tab id "pane"/);
    } finally {
      consoleError.mockRestore();
    }
  });

  test("honors a custom relation widget in the overview and saves its selected id", async () => {
    sdkMocks.record = {
      id: "client-1", displayName: "Acme",
      reviewer: { id: "reviewer-1", displayName: "Reviewer One" },
    };
    const custom = {
      read: () => <span>Custom reviewer display</span>,
      edit: ({ value, onChange }: { value?: unknown; onChange?: (value: unknown) => void }) => (
        <input aria-label="Custom reviewer" value={String(value ?? "")} onChange={(event) => onChange?.(event.target.value)} />
      ),
    };
    renderWithProviders(
      <FormView resource="OAuthClient" id="client-1" fields={[
        { name: "displayName", label: "Name", title: true },
        { name: "reviewer", label: "Reviewer", widget: "test.reviewer" },
      ]} />,
      { types: {
        OAuthClientType: {
          ...defaultModel("OAuthClientType", "OAuthClient"),
          fields: {
            displayName: { name: "displayName", kind: "scalar", scalar: "String" },
            reviewer: { name: "reviewer", kind: "relation", relationModelLabel: "Widget", relationObject: true },
          },
        },
        WidgetType: {
          ...defaultModel("WidgetType", "Widget"),
          fields: { displayName: { name: "displayName", kind: "scalar", scalar: "String" } },
          resource: { ...defaultResource("WidgetType", "Widget"), recordRepresentation: "displayName" },
        },
      } }, undefined,
      { widgets: { ...defaultWidgets, "test.reviewer": custom } },
    );
    const input = await screen.findByRole("textbox", { name: "Custom reviewer" });
    expect(screen.queryByRole("button", { name: "Reviewer" })).toBeNull();
    fireEvent.change(input, { target: { value: "reviewer-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { id: "client-1", reviewer: "reviewer-2" } }));
  });

  test("reads many2one record ids and writes the flat relation field", async () => {
    sdkMocks.record = {
      id: "client-1",
      displayName: "Acme",
      reviewer: { id: "reviewer-1", displayName: "Reviewer One" },
    };
    const relationFields = [
      { name: "displayName", label: "Display Name", title: true },
      {
        name: "reviewer",
        label: "Reviewer",
        widget: "many2one",
        options: [
          { value: "reviewer-1", label: "Reviewer One" },
          { value: "reviewer-2", label: "Reviewer Two" },
        ],
      },
    ] satisfies readonly FormField[];

    renderWithProviders(
      <FormView
        resource="OAuthClient"
        id="client-1"
        fields={relationFields}
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Reviewer/ }).textContent).toContain(
        "Reviewer One",
      ),
    );
    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "Acme Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { displayName: "Acme Renamed", id: "client-1" },
    });

    cleanup();
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    renderWithProviders(
      <FormView
        resource="OAuthClient"
        fields={relationFields}
        defaultValues={{ reviewer: "reviewer-2" }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { displayName: "", reviewer: "reviewer-2" },
    });
  });

  test("keeps saved values after a parent re-render with new field descriptors", async () => {
    function Harness(): ReactElement {
      const [saveVersion, setSaveVersion] = useState(0);
      const viewFields = useMemo(() => cloneFields(fields), [saveVersion]);

      return (
        <>
          <span data-testid="save-version" hidden>
            {saveVersion}
          </span>
          <FormView
            resource="notes.Note"
            id="note-1"
            fields={viewFields}
            onSaved={() => {
              setSaveVersion((current) => current + 1);
            }}
          />
        </>
      );
    }

    renderWithProviders(<Harness />);

    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );

    fireEvent.change(title, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("save-version").textContent).toBe("1"),
    );
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull(),
    );
    await act(async () => {
      await nextTask();
    });

    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
      "Renamed",
    );
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });

  test("does not block navigation after a successful save resets dirty state", async () => {
    let router: ReturnType<typeof createRouter> | undefined;

    function Root(): ReactElement {
      return <TestProviders><Outlet /></TestProviders>;
    }

    const rootRoute = createRootRoute({ component: Root });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => (
        <FormView
          resource="notes.Note"
          id="note-1"
          fields={fields}
          onSaved={() => {
            void router?.navigate({ to: "/next" });
          }}
        />
      ),
    });
    const nextRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/next",
      component: () => <span>Next route</span>,
    });
    router = createRouter({
      routeTree: rootRoute.addChildren([indexRoute, nextRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
    render(<RouterProvider router={router} />);

    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );

    fireEvent.change(title, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(router?.state.location.pathname).toBe("/next"));
    expect(await screen.findByText("Next route")).toBeTruthy();
    expect(screen.queryByText(/Unsaved changes/)).toBeNull();
  });

  test("preserves the current URL while dirty navigation is blocked and leaves only after confirmation", async () => {
    let router: ReturnType<typeof createRouter> | undefined;

    function Root(): ReactElement {
      return <TestProviders><Outlet /></TestProviders>;
    }

    const rootRoute = createRootRoute({ component: Root });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => <FormView resource="notes.Note" id="note-1" fields={fields} />,
    });
    const nextRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/next",
      component: () => <span>Next route</span>,
    });
    router = createRouter({
      routeTree: rootRoute.addChildren([indexRoute, nextRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
    render(<RouterProvider router={router} />);

    const title = await screen.findByLabelText("Title");
    fireEvent.change(title, { target: { value: "Unsaved" } });
    void router.navigate({ to: "/next" });

    expect(await screen.findByText("Unsaved changes - leave without saving?")).toBeTruthy();
    expect(router.state.location.pathname).toBe("/");
    fireEvent.click(screen.getByRole("button", { name: "Stay" }));
    await waitFor(() => expect(router?.state.location.pathname).toBe("/"));

    void router.navigate({ to: "/next" });
    fireEvent.click(await screen.findByRole("button", { name: "Leave" }));
    await waitFor(() => expect(router?.state.location.pathname).toBe("/next"));
  });

  test("seeds a new record clean after leaving a dirty record", async () => {
    const records: Record<string, Row> = {
      "note-1": { id: "note-1", title: "First", status: "ACTIVE" },
      "note-2": { id: "note-2", title: "Second", status: "ACTIVE" },
    };

    function Root(): ReactElement {
      return <TestProviders><Outlet /></TestProviders>;
    }

    function RecordPage(): ReactElement {
      const { recordId } = recordRoute.useParams();
      sdkMocks.record = records[recordId] ?? null;
      return <FormView resource="notes.Note" id={recordId} fields={fields} />;
    }

    const rootRoute = createRootRoute({ component: Root });
    const recordRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/$recordId",
      component: RecordPage,
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([recordRoute]),
      history: createMemoryHistory({ initialEntries: ["/note-1"] }),
    });
    render(<RouterProvider router={router} />);

    const firstTitle = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((firstTitle as HTMLInputElement).value).toBe("First"),
    );
    fireEvent.change(firstTitle, { target: { value: "A edit must not bleed" } });

    void router.navigate({ to: "/$recordId", params: { recordId: "note-2" } });
    fireEvent.click(await screen.findByRole("button", { name: "Leave" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/note-2"));
    const secondTitle = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((secondTitle as HTMLInputElement).value).toBe("Second"),
    );
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

    fireEvent.change(secondTitle, { target: { value: "Second edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Second edited", id: "note-2" },
    });
    expect(JSON.stringify(sdkMocks.mutate.mock.calls)).not.toContain(
      "A edit must not bleed",
    );
  });

  test("mounts record tab panels lazily and returns to the overview form", async () => {
    const mountPanel = vi.fn();
    const unmountPanel = vi.fn();
    function ActivityPanel(): ReactElement {
      useEffect(() => {
        mountPanel();
        return unmountPanel;
      }, []);
      return <span>Activity panel</span>;
    }
    const renderPanel = vi.fn(() => <ActivityPanel />);
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        recordTabs={[{ id: "activity", label: "Activity", render: renderPanel }]}
      />,
    );

    await screen.findByLabelText("Title");
    // The descriptor factory constructs the panel element as FormView renders;
    // the returned component itself stays inert until its tab is activated.
    expect(renderPanel).toHaveBeenCalled();
    expect(mountPanel).not.toHaveBeenCalled();
    expect(screen.queryByText("Activity panel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(await screen.findByText("Activity panel")).toBeTruthy();
    expect(mountPanel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(screen.getByLabelText("Title")).toBeTruthy();
    // Base UI unmounts the outgoing panel when its exit transition completes.
    await waitFor(() => {
      expect(screen.queryByText("Activity panel")).toBeNull();
      expect(unmountPanel).toHaveBeenCalledTimes(1);
    });
  });

  test("workspace records open the validated default panel with Overview first", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        recordPresentation="workspace"
        defaultRecordTab="editor"
        recordExtras={() => <p>Related records</p>}
        recordTabs={[
          { id: "editor", label: "Editor", keepMounted: true, render: ({ active }) => <>
            <button type="button">Editor action</button><output data-testid="retained-panel-active">{String(active)}</output>
          </> },
          { id: "runs", label: "Runs", render: () => <p>Runs panel</p> },
        ]}
      />,
    );

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Overview", "Editor", "Runs"]);
    expect(screen.getByRole("button", { name: "Editor action" })).toBeTruthy();
    expect(screen.getByTestId("retained-panel-active").textContent).toBe("true");
    expect(await screen.findByText("Active")).toBeTruthy();
    expect(screen.queryByText("ACTIVE")).toBeNull();
    expect(screen.queryByLabelText("Reminder")).toBeNull();
    expect(screen.queryByText("Related records")).toBeNull();
    expect(document.querySelector("form")?.className).toContain("contents");

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(await screen.findByLabelText("Reminder")).toBeTruthy();
    expect(screen.getByText("Related records")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Editor action" })).toBeNull();
    expect(screen.getByTestId("retained-panel-active").textContent).toBe("false");
  });

  test("workspace record panels keep full-height content beside one rail", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
      recordPresentation="workspace" defaultRecordTab="editor"
      recordTabs={[{ id: "editor", label: "Editor", keepMounted: true,
        render: () => <div data-testid="workspace-canvas" className="h-full">Canvas</div> }]}
    />, undefined, undefined, formChildren({ "notes.Note#rail": { "notes.properties": {
      content: <FormView.RailGroup id="properties" label="Properties" content={<p>Summary</p>} />,
    } } }));

    const panel = await screen.findByRole("tabpanel", { name: "Editor" });
    expect(within(panel).getByTestId("workspace-canvas")).toBeTruthy();
    expect(panel.className).toContain("flex-1");
    expect(panel.firstElementChild?.className).toContain("h-full");
    expect(panel.innerHTML).not.toContain("max-w-[1100px]");
    expect(within(panel).getByRole("complementary")).toBeTruthy();
    expect(document.querySelectorAll("aside")).toHaveLength(1);
  });

  test("full-bleed and document tabs share their header and tab geometry while retaining the editor draft", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} defaultRecordTab="editor"
      recordTabs={[
        { id: "editor", label: "Editor", presentation: "full-bleed", keepMounted: true,
          render: () => <input aria-label="Editor draft" defaultValue="Original" /> },
        { id: "activity", label: "Activity", render: () => <p>Activity content</p> },
      ]} />);
    const editor = await screen.findByRole("tabpanel", { name: "Editor" });
    const draft = within(editor).getByRole("textbox", { name: "Editor draft" });
    fireEvent.change(draft, { target: { value: "Unsaved draft" } });
    expect(editor.className).toContain("flex-1");
    expect(editor.className).toContain("overflow-hidden");
    expect(editor.closest('[data-tabs-root]')?.className ?? editor.parentElement?.className).toContain("h-full");
    expect(editor.innerHTML).not.toContain("max-w-[1100px]");
    expect(editor.firstElementChild?.className).toContain("h-full");
    expect(draft.parentElement?.parentElement?.className).toContain("h-full");
    expect(draft.parentElement?.parentElement?.className).toContain("grid-rows-[minmax(0,1fr)]");
    const recordChrome = () => {
      const title = screen.getByRole("textbox", { name: "Title" });
      const header = title.closest("header")!;
      const strip = screen.getByRole("tablist");
      expect(header.className).toContain("gap-4");
      expect(title.className).toContain("text-28");
      return { headerClasses: header.parentElement!.className, stripClasses: strip.className };
    };
    const chrome = recordChrome();
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    const activity = await screen.findByRole("tabpanel", { name: "Activity" });
    expect(activity.className).toContain("max-w-[1100px]");
    expect(recordChrome()).toEqual(chrome);
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(await screen.findByLabelText("Reminder")).toBeTruthy();
    expect(recordChrome().stripClasses).toBe(chrome.stripClasses);
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    expect(recordChrome()).toEqual(chrome);
    expect(screen.getByRole("textbox", { name: "Editor draft" })).toBe(draft);
    expect(draft).toHaveProperty("value", "Unsaved draft");
  });

  test("workspace records without tabs keep the compact header and scrolling form body", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        recordPresentation="workspace"
        readOnly
        hideRecordChrome
      />,
    );

    const heading = await screen.findByRole("heading", { name: "First" });
    expect(heading.className).toContain("text-base");
    expect(heading.className).not.toContain("text-28");
    expect(within(heading.closest("header")!).getByText("Active").className).toContain("justify-self-start");
    expect(document.querySelector("form")?.className).toContain("min-h-0");
    expect(heading.closest("form")?.querySelector(".overflow-auto")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  test("read-only records retain declared lifecycle actions while generated edits stay hidden", async () => {
    const run = vi.fn(async () => undefined);
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly>
      <Field name="title" label="Title" title />
      <Action id="review" label="Review" placement="toolbar" primary run={run} />
      <Action id="rename" label="Rename" set={{ title: "Changed" }} />
    </FormView>);
    expect(await screen.findByRole("heading", { name: "First" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Rename" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(run).toHaveBeenCalledOnce());
  });

  test("document records keep Overview before record tabs without changing presentation", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        defaultRecordTab="activity"
        recordTabs={[
          { id: "activity", label: "Activity", render: () => <p>Activity panel</p> },
        ]}
      />,
    );

    expect((await screen.findAllByRole("tab")).map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Activity",
    ]);
    expect(screen.getByText("Activity panel")).toBeTruthy();
    expect(document.querySelector("form")?.className).toContain("min-h-full");
  });

  test("workspace tab switches preserve dirty overview fields and reset to the default for a new record", async () => {
    function Harness(): ReactElement {
      const [id, setId] = useState("note-1");
      return <>
        <button type="button" onClick={() => setId("note-2")}>Next note</button>
        <FormView resource="notes.Note" id={id} fields={fields}
          recordPresentation="workspace" defaultRecordTab="editor"
          recordTabs={[{ id: "editor", label: "Editor", render: () => <p>Editor panel</p> }]} />
      </>;
    }
    renderWithProviders(<Harness />);

    expect(await screen.findByText("Editor panel")).toBeTruthy();
    const title = await screen.findByLabelText("Title");
    fireEvent.change(title, { target: { value: "Dirty title" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(await screen.findByLabelText("Reminder")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Dirty title");

    sdkMocks.record = { ...sdkMocks.record, id: "note-2", title: "Second" };
    fireEvent.click(screen.getByRole("button", { name: "Next note" }));
    expect(await screen.findByText("Editor panel")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Editor" }).getAttribute("aria-selected")).toBe("true");
  });

  test("workspace falls back to the form for invalid defaults and create", async () => {
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" fields={fields}
        recordPresentation="workspace" defaultRecordTab="removed"
        recordTabs={[{ id: "editor", label: "Editor", render: () => <p>Editor panel</p> }]} />,
    );
    expect(await screen.findByLabelText("Title")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");

    cleanup();
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note" id={null} fields={fields}
        recordPresentation="workspace" defaultRecordTab="editor"
        recordTabs={[{ id: "editor", label: "Editor", render: () => <p>Editor panel</p> }]} />,
    );
    expect(await screen.findByLabelText("Title")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Editor" })).toBeNull();
  });

  test("resolves record-dependent read-only fields in the header, body, and groups", async () => {
    const resolvedFields = [
      {
        name: "title",
        label: "Title",
        title: true,
        resolve: (record: Row) => ({
          name: "title",
          title: true,
          readOnly: record.status === "PUBLISHED",
        }),
      },
      {
        name: "description",
        label: "Description",
        body: true,
        kind: "textarea",
        resolve: (record: Row) => ({
          name: "description",
          readOnly: record.status === "PUBLISHED",
        }),
      },
      { name: "status", label: "Status", defaultValue: "DRAFT" },
      {
        name: "wordCount",
        label: "Word Count",
        resolve: (record: Row) => ({
          name: "wordCount",
          readOnly: record.status === "PUBLISHED",
        }),
      },
    ] satisfies readonly FormField[];

    sdkMocks.record = {
      id: "note-1",
      title: "Published note",
      description: "Frozen body",
      status: "PUBLISHED",
      wordCount: 3,
    };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" fields={resolvedFields} />,
    );
    expect(await screen.findByRole("heading", { name: "Published note" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull();
    expect(screen.queryByRole("spinbutton", { name: "Word Count" })).toBeNull();

    cleanup();
    sdkMocks.record = {
      id: "note-2",
      title: "Draft note",
      description: "Editable body",
      status: "DRAFT",
      wordCount: 2,
    };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-2" fields={resolvedFields} />,
    );
    expect(await screen.findByRole("textbox", { name: "Title" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Description" })).toBeTruthy();

    cleanup();
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note" id={null} fields={resolvedFields} />,
    );
    expect(await screen.findByRole("textbox", { name: "Title" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Description" })).toBeTruthy();
  });

  test("honors resolve from JSX Field declarations", async () => {
    sdkMocks.record = {
      id: "note-1",
      title: "Published JSX note",
      status: "PUBLISHED",
      wordCount: 3,
    };
    const readOnlyWhenPublished = (record: Row) => ({
      name: "wordCount",
      readOnly: record.status === "PUBLISHED",
    });
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field
          name="title"
          title
          resolve={(record) => ({
            name: "title",
            title: true,
            readOnly: record.status === "PUBLISHED",
          })}
        />
        <Field name="status" />
        <Field name="wordCount" resolve={readOnlyWhenPublished} />
      </FormView>,
    );

    expect(await screen.findByRole("heading", { name: "Published JSX note" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.queryByRole("spinbutton", { name: "Word Count" })).toBeNull();
  });

  test("mounts keepMounted record panels eagerly from the first render", async () => {
    const mountPanel = vi.fn();
    function ActivityPanel(): ReactElement {
      useEffect(() => {
        mountPanel();
      }, []);
      return <span>Keep-mounted activity</span>;
    }

    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        recordTabs={[
          {
            id: "activity",
            label: "Activity",
            keepMounted: true,
            render: () => <ActivityPanel />,
          },
        ]}
      />,
    );

    await screen.findByLabelText("Title");
    expect(mountPanel).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Keep-mounted activity")).toBeTruthy();
  });

  test("projects only fields the SDL read type exposes (skips write-only inputs)", async () => {
    // A write-only input (password) is declared on the form but absent from the
    // read type. Selecting it would make the whole detail/return query invalid
    // and the record would load as null (every field blank, "Untitled").
    const metadata: TestSchemaMetadata = {
      types: {
        UserType: {
          ...defaultModel("UserType", "iam.User"),
          fields: {
            username: { name: "username", kind: "scalar", scalar: "String" },
            email: { name: "email", kind: "scalar", scalar: "String" },
            reviewer: {
              name: "reviewer",
              kind: "relation",
              relationModelLabel: "Reviewer",
            },
          },
          resource: {
            ...defaultResource("UserType", "iam.User"),
            recordRepresentation: "username",
          },
        },
      },
    };
    sdkMocks.record = { id: "user-1", username: "ada", email: "ada@x.io" };

    renderWithProviders(
      <FormView
        resource="iam.User"
        id="user-1"
        fields={[
          { name: "username", label: "Username", title: true },
          { name: "email", label: "Email" },
          { name: "reviewer", label: "Reviewer", widget: "many2one" },
          { name: "password", label: "Password", createOnly: true },
        ]}
      />,
      metadata,
    );

    await waitFor(() => expect(sdkMocks.recordSelection).toBeDefined());
    const selection = sdkMocks.recordSelection ?? [];
    expect(selection).toContain("id");
    expect(selection).toContain("username");
    expect(selection).toContain("email");
    expect(selection).toContain("reviewer"); // scalar-id relation → bare leaf
    expect(selection).not.toContain("reviewer.id");
    expect(selection).not.toContain("password"); // write-only → never read back
  });

  test("selects and renders declared nested subtitle facts", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = {
      id: "page-1",
      title: "Metadata",
      created_at: "2026-08-20T10:00:00Z",
      updated_at: "2026-08-21T10:00:00Z",
      markdown: { word_count: 321 },
    };
    const resource = {
      ...defaultResource("PageType", "knowledge.Page"),
      subtitle: {
        created: "created_at",
        updated: "updated_at",
        wordCount: "markdown.word_count",
      },
    };
    const metadata: TestSchemaMetadata = {
      types: {
        PageType: {
          ...defaultModel("PageType", "knowledge.Page"),
          fields: {
            title: { name: "title", kind: "scalar", scalar: "String" },
            created_at: { name: "created_at", kind: "scalar", scalar: "DateTime" },
            updated_at: { name: "updated_at", kind: "scalar", scalar: "DateTime" },
            markdown: { name: "markdown", kind: "relation" },
          },
          resource,
        },
      },
      resources: [resource],
    };

    renderWithProviders(
      <FormView resource="knowledge.Page" id="page-1">
        <Field name="title" label="Title" title />
      </FormView>,
      metadata,
    );

    expect(await screen.findByText("321 words")).toBeTruthy();
    expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining([
      "created_at",
      "updated_at",
      "markdown.word_count",
    ]));
  });

  test("blocks create and flags a missing required field in an inactive form tab", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    const metadata: TestSchemaMetadata = {
      types: {
        NoteType: {
          ...defaultModel("NoteType", "notes.Note"),
          fields: {
            title: { name: "title", kind: "scalar", scalar: "String" },
            deadline: { name: "deadline", kind: "scalar", scalar: "DateTime" },
          },
          resource: {
            ...defaultResource("NoteType", "notes.Note"),
            roots: {
              ...defaultResource("NoteType", "notes.Note").roots,
              create: "createNote",
            },
            requiredCreateFields: ["deadline"],
          },
        },
      },
    };

    renderWithProviders(
      <FormView resource="notes.Note" layout="tabs">
        <Group label="Overview">
          <Field name="title" label="Title" />
        </Group>
        <Group label="Schedule">
          <Field name="deadline" label="Deadline" />
        </Group>
      </FormView>,
      metadata,
    );
    fireEvent.keyDown(screen.getByLabelText("Title"), { key: "Enter" });

    expect(sdkMocks.mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "Schedule" }));

    // The unmounted required field is retained as an inline error when its tab opens.
    await screen.findByText("This field is required.");
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("renders server validation errors under their field and in the banner", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    sdkMocks.mutate.mockRejectedValue({
      graphQLErrors: [
        {
          message: "Validation failed.",
          extensions: {
            code: "VALIDATION",
            validationErrors: {
              reminderAt: ["This field cannot be blank."],
            },
            formErrors: ["Note is misconfigured."],
          },
        },
      ],
    });

    renderWithProviders(<FormView resource="notes.Note" fields={fields} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    // Field message renders under the Reminder field…
    await screen.findByText("This field cannot be blank.");
    // …and the form-level message stays in the banner.
    expect(screen.getByText("Note is misconfigured.")).toBeTruthy();
  });

  test("renders and submits a field only when its showWhen discriminator matches", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    renderWithProviders(
      <FormView resource="notes.Note">
        <Field name="kind" label="Kind" />
        <Field
          name="secret"
          label="Secret"
          showWhen={(values) => values.kind === "static"}
        />
      </FormView>,
    );

    // Hidden until the discriminator matches.
    expect(screen.queryByLabelText("Secret")).toBeNull();

    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "static" } });
    fireEvent.change(await screen.findByLabelText("Secret"), {
      target: { value: "s3cret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { kind: "static", secret: "s3cret" },
    });
  });

  test("drops a showWhen field from the payload once the discriminator flips away", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    renderWithProviders(
      <FormView resource="notes.Note">
        <Field name="kind" label="Kind" />
        <Field
          name="secret"
          label="Secret"
          showWhen={(values) => values.kind === "static"}
        />
      </FormView>,
    );

    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "static" } });
    fireEvent.change(await screen.findByLabelText("Secret"), {
      target: { value: "s3cret" },
    });
    // Flip the discriminator: the secret is hidden and excluded from the payload.
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "ssh" } });
    await waitFor(() => expect(screen.queryByLabelText("Secret")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { kind: "ssh" } });
  });

  test("uses an addon-registered form override on create, not on edit", async () => {
    const override = <Field name="overrideName" label="Override Name" />;

    // Create (id null): the override replaces the declared fields.
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    renderWithProviders(
      <FormView resource="Widget">
        <Field name="declaredName" label="Declared Name" />
      </FormView>,
      undefined,
      { Widget: override },
    );
    expect(screen.getByLabelText("Override Name")).toBeTruthy();
    expect(screen.queryByLabelText("Declared Name")).toBeNull();

    cleanup();

    // Edit (id set): the override is ignored; the declared lifecycle form renders.
    sdkMocks.record = { id: "w-1", declaredName: "kept" };
    renderWithProviders(
      <FormView resource="Widget" id="w-1">
        <Field name="declaredName" label="Declared Name" />
      </FormView>,
      undefined,
      { Widget: override },
    );
    expect(await screen.findByLabelText("Declared Name")).toBeTruthy();
    expect(screen.queryByLabelText("Override Name")).toBeNull();
  });

  test("shows a title-field server error in the header", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    sdkMocks.mutate.mockRejectedValue({
      graphQLErrors: [
        {
          message: "Validation failed.",
          extensions: {
            code: "VALIDATION",
            validationErrors: {
              title: ["This field cannot be blank."],
              environment: ["This field cannot be blank."],
            },
            formErrors: [],
          },
        },
      ],
    });

    renderWithProviders(<FormView resource="notes.Note" fields={fields} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await screen.findByText("This field cannot be blank.");
    // Server-only issues retain both their field name and their actionable reason.
    expect(
      screen.getByText("Please fix the highlighted fields: Title. environment: This field cannot be blank."),
    ).toBeTruthy();
  });
});

/** The form's containers with no children, as a composed app declares them. */
const NO_FORM_CHILDREN = containersFromChildren(FORM_CONTAINERS, {});

/** The runtime override placing children straight into the form's containers. */
function formChildren(
  children: Readonly<Record<string, Readonly<Record<string, ContainerChild>>>>,
): Partial<AppRuntime> {
  return { containers: containersFromChildren(FORM_CONTAINERS, children) };
}

function renderForm(id: string | null): void {
  renderWithProviders(<FormView resource="notes.Note" id={id} fields={fields} />);
}

function renderWithProviders(
  children: ReactElement,
  metadata?: TestSchemaMetadata,
  forms?: FormOverrideMap,
  runtime?: Partial<AppRuntime>,
): void {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <TestProviders metadata={metadata} forms={forms} runtime={runtime}>
      <RouterContextProvider router={router}>{children}</RouterContextProvider>
    </TestProviders>,
  );
}

const { Provider, dataProvider, clients, clearClients } = createUiTestProviders({
  apiUrl: "test://forms",
  queryClientConfig: { defaultOptions: { mutations: { retry: false }, queries: { retry: false } } },
  dataProvider: {
    getOne: sdkMocks.getOne,
    getList: sdkMocks.getList,
    create: async ({ variables }: { variables: Row }) => {
      const data = await sdkMocks.mutate({ data: variables });
      sdkMocks.record = data;
      return { data };
    },
    update: vi.fn(async ({ id, variables }: { id: string; variables: Row }) => {
      const data = await sdkMocks.mutate({ data: { ...variables, id } });
      sdkMocks.record = data;
      return { data };
    }),
  },
});

const notificationProvider = {
  open: vi.fn(),
  close: vi.fn(),
} satisfies NotificationProvider;

function TestProviders({ children, metadata, forms, runtime }: {
  children: ReactNode;
  metadata?: TestSchemaMetadata;
  forms?: FormOverrideMap;
  runtime?: Partial<AppRuntime>;
}): ReactElement {
  const schema = useMemo(() => withDefaultResourceMetadata(metadata), [metadata]);
  return (
    <Provider
      metadata={schema}
      notificationProvider={notificationProvider}
    >
      <ModalsHost>
        <ToastProvider>
          <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers: NO_FORM_CHILDREN, ...(forms ? { forms } : {}), ...runtime }}>
            {children}
          </AppRuntimeProvider>
        </ToastProvider>
      </ModalsHost>
    </Provider>
  );
}

function fieldsFromMeta(meta: GetOneParams["meta"]): string[] {
  const paths: string[] = [];
  const visit = (items: readonly unknown[], prefix = ""): void => {
    for (const item of items) {
      if (typeof item === "string") {
        paths.push(prefix ? `${prefix}.${item}` : item);
      } else if (item && typeof item === "object") {
        for (const [key, value] of Object.entries(item)) {
          if (Array.isArray(value)) visit(value, prefix ? `${prefix}.${key}` : key);
        }
      }
    }
  };
  if (Array.isArray(meta?.fields)) visit(meta.fields);
  return paths;
}

function withDefaultResourceMetadata(
  metadata: TestSchemaMetadata | undefined,
): SchemaFieldMetadata {
  const seed = metadata ?? { types: {} };
  const types: Record<string, ModelMetadata> = {
    NoteType: defaultModel("NoteType", "notes.Note"),
    RepositoryType: defaultModel("RepositoryType", "integrate_vcs.Repository"),
    InferenceModelType: defaultModel("InferenceModelType", "agents.InferenceModel"),
    IntegrationType: defaultModel("IntegrationType", "integrate.Integration"),
    OAuthClientType: defaultModel("OAuthClientType", "OAuthClient"),
    UserType: defaultModel("UserType", "iam.User"),
    WidgetType: defaultModel("WidgetType", "Widget"),
    ...seed.types,
  };
  for (const [typeName, model] of Object.entries(types)) {
    const modelLabel = modelLabelForType(typeName);
    types[typeName] = {
      ...defaultModel(typeName, modelLabel),
      ...model,
      resource: model.resource ?? defaultResource(typeName, modelLabel),
    };
  }
  return withTestResourceInventory({ ...seed, types });
}

function defaultModel(typeName: string, modelLabel: string): ModelMetadata {
  return {
    fields: {},
    resource: defaultResource(typeName, modelLabel),

  };
}

function defaultResource(typeName: string, modelLabel: string): DataResourceMetadata {
  return {
    ...testDataResource(modelLabel, { typeNames: { node: typeName } }),
    modelName: modelLabelSegment(modelLabel),
  };
}

function workflowRelationMetadata(): TestSchemaMetadata {
  return {
    types: {
      RunType: {
        ...defaultModel("RunType", "workflows.Run"),
        fields: {
          workflow: {
            name: "workflow",
            kind: "relation",
            relationModelLabel: "workflows.Workflow",
          },
        },
      },
      WorkflowType: {
        ...defaultModel("WorkflowType", "workflows.Workflow"),
        fields: {
          name: { name: "name", kind: "scalar", scalar: "String" },
        },
        resource: {
          ...defaultResource("WorkflowType", "workflows.Workflow"),
          recordRepresentation: "name",
        },
      },
    },
  };
}

/**
 * An MTI pair: `notes.Note` reports `parties.Party` as its canonical parent, so
 * a verb contributed against the parent reaches the subtype's form. The parent
 * is its own canonical label, as the backend emits it.
 */
function mtiMetadata(): SchemaFieldMetadata {
  return withTestResourceInventory({
    types: {
      NoteType: mtiModel("NoteType", "notes.Note", "parties.Party"),
      PartyType: mtiModel("PartyType", "parties.Party", "parties.Party"),
    },
  });
}

function mtiModel(
  typeName: string,
  modelLabel: string,
  canonicalLabel: string,
  implFields?: readonly string[],
): ModelMetadata {
  return {
    ...defaultModel(typeName, modelLabel),
    fields: { title: { name: "title", kind: "scalar", scalar: "String" } },
    resource: {
      ...defaultResource(typeName, modelLabel),
      canonicalLabel,
      ...(implFields ? { implFields } : {}),
    },
  };
}

/**
 * The MTI pair above, with the subtype declaring an impl column — the shape the
 * backend emits for `messaging.Channel` (`implFields: ["backend_class"]`, its
 * canonical parent `integrate.Integration`).
 *
 * `kind` is a readable model field and *not* a declared form field, which is the
 * shape that matters: `_impl_fields` intersects with the resource's readable
 * columns, never with any form's selection, so a form reaches its impl value only
 * because `FormView` selects it.
 */
function implMetadata(extra: ModelMetadata["fields"] = {}): SchemaFieldMetadata {
  const note = mtiModel("NoteType", "notes.Note", "parties.Party", ["kind"]);
  return withTestResourceInventory({
    types: {
      NoteType: {
        ...note,
        fields: { ...note.fields, kind: { name: "kind", kind: "scalar", scalar: "String" }, ...extra },
      },
      PartyType: mtiModel("PartyType", "parties.Party", "parties.Party"),
    },
  });
}

function modelLabelForType(typeName: string): string {
  const known: Record<string, string> = {
    NoteType: "notes.Note",
    PartyType: "parties.Party",
    RepositoryType: "integrate_vcs.Repository",
    InferenceModelType: "agents.InferenceModel",
    IntegrationType: "integrate.Integration",
    OAuthClientType: "OAuthClient",
    UserType: "iam.User",
    WidgetType: "Widget",
  };
  return known[typeName] ?? typeName.replace(/Type$/, "");
}

function cloneFields(source: readonly FormField[]): FormField[] {
  return source.map((field) => ({ ...field }));
}

/** The rendered buttons among `labels`, in document order. */
function buttonLabels(labels: readonly string[]): string[] {
  return screen
    .getAllByRole("button")
    .map((button) => button.textContent ?? "")
    .filter((label) => labels.includes(label));
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
