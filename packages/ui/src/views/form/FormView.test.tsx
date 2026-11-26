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
import { modelLabelSegment, schemaFieldMetadataFromDataResources } from "@angee/metadata";
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
import { useFormState } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { createUiTestProviders } from "../../testing";
import { DraftScopeProvider } from "../../lib/idempotency-key";
import { defaultWidgets, type WidgetRenderProps } from "../../widgets";
import { deserializeFormSpec } from "./form-spec";
import { BoundDescriptorField } from "./BoundDescriptorField";
import { Form } from "./Form";
import {
  FormView,
  FORM_CONTAINERS,
  type FormField,
  type FormSubmit,
  type FormSubmitContext,
} from "./FormView";
import { useFormViewSurface, type UseFormViewSurfaceProps } from "./form-view-surface";
import { emptyDraft } from "./form-view-model";
import { ActionTrigger } from "../../toolbars/ActionMenu";
import { useRecordChromeContext } from "../resource/record-chrome-context";
import { RecordFieldMarksProvider, useRecordFieldMarks } from "./record-field-marks";
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

// One FormSpec property per value rule, a value breaking it, and what each field then says.
const ruleSchemaProperties = {
  handle: { type: "string", label: "Handle", minLength: 3 },
  code: { type: "string", label: "Code", maxLength: 4 },
  low: { type: "integer", label: "Low", minimum: 5 },
  high: { type: "integer", label: "High", maximum: 10 },
  count: { type: "integer", label: "Count", omittable: true },
  tags: { type: "array", widget: "list", label: "Tags", minItems: 2, items: { type: "string" } },
  limits: { type: "array", widget: "list", label: "Limits", maxItems: 1, items: { type: "string" } },
} as const;
const ruleValues = { handle: "ab", code: "abcde", low: 2, high: 11, count: "", tags: ["a"], limits: ["a", "b"] };
const ruleMessages = {
  Handle: "Enter at least 3 characters.",
  Code: "Enter no more than 4 characters.",
  Low: "Enter a value of at least 5.",
  High: "Enter a value of at most 10.",
  Count: "Enter a valid whole number.",
  Tags: "Add at least 2 items.",
  Limits: "Keep no more than 1 item.",
};

// A JSON root's FormSpec members shown as their own fields, as an implementation's config is.
function configFields(properties: Readonly<Record<string, Readonly<Record<string, unknown>>>>) {
  return deserializeFormSpec({ type: "object", properties }, defaultWidgets)
    .map((field) => ({ ...field, name: `config.${field.name}` }));
}
const timeoutMember = { type: "integer", label: "Timeout", omittable: true } as const;

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

  test("renders a lone declared record action as a secondary button", async () => {
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
      </FormView>,
    );

    const archive = await screen.findByRole("button", { name: "Archive" });
    expect(archive.classList.contains("bg-inset")).toBe(true);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test.each([null, undefined])("a create form offers only Create, without own, contributed or generic record verbs (id=%s)", async (id) => {
    sdkMocks.record = null;
    const run = vi.fn();
    renderWithProviders(<FormView resource="notes.Note" id={id}
      deleteAction={{ canDelete: true, isPending: false, onDelete: run }}
      actions={[{ id: "own-menu", label: "Own menu verb", placement: "menu", run }]}>
      <Field name="title" label="Title" title />
      <Action id="refresh" label="Refresh" run={run} />
      <Action id="review" label="Review" primary submit={run} />
      <Action id="rename" label="Rename" placement="toolbar" set={{ title: "Renamed" }} />
    </FormView>, recordActionsMetadata(), undefined, formChildren({
      "notes.Note#sections": {
        "notes.inspect": { content: <Action id="inspect" label="Inspect" run={run} /> },
      },
      "notes.Note#actions": { "notes.publish": { content: <ActionTrigger>Publish</ActionTrigger> } },
      "notes.Note#actions-menu": { "notes.export": { content: <ActionTrigger>Export</ActionTrigger> } },
    }));

    const create = await screen.findByRole("button", { name: "Create" });
    expect(create).toHaveProperty("disabled", false);
    expect(within(create.closest('[role="toolbar"]')!).getAllByRole("button")).toEqual([create]);
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(run).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "New" } });
    fireEvent.click(create);
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { title: "New" } }));
    expect(run).not.toHaveBeenCalled();
  });

  test.each([false, true])("a section verb stays in the menu without demoting the host's lone verb (primary: %s)", async (primary) => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Action id="publish" label="Publish" run={vi.fn()} />
    </FormView>, undefined, undefined, formChildren({ "notes.Note#sections": {
      "imap.reindex": { content: <Action id="reindex" label="Reindex IMAP" primary={primary} run={vi.fn()} /> },
    } }));

    const publish = await screen.findByRole("button", { name: "Publish" });
    expect(publish.classList.contains("bg-inset")).toBe(true);
    expect(screen.queryByRole("button", { name: "Reindex IMAP" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Reindex IMAP" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Publish" })).toBeNull();
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
        permissions: { name: "permissions", kind: "list", scalar: "String" },
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
    expect(screen.queryByRole("heading", { name: "Private tab" })).toBeNull();

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    renderSections();
    await screen.findByDisplayValue("First");
    expect(screen.getByText("Private")).toBeTruthy();
    // The record's one pane renders beneath the sheet under its heading.
    expect(screen.getByRole("heading", { name: "Private tab" })).toBeTruthy();
  });

  test("Action declarations and record-verb children require their declared permission", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read"] };
    const renderActions = () => renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" title />
      <Action id="archive" label="Archive" permission="write" run={vi.fn()} />
    </FormView>, recordActionsMetadata(), undefined, formChildren({ "notes.Note#actions": {
      "notes.reopen": { permission: "write", content: <button type="button">Reopen</button> },
    } }));
    renderActions();
    await screen.findByRole("heading", { name: "First" });
    expect(screen.queryByRole("button", { name: "Reopen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    renderActions();
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Archive" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test("the Actions menu is the band's last verb control, after the form's own and contributed buttons", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" title />
      <Action id="sync" label="Sync now" permission="write" run={vi.fn()} />
      <Action id="copy" label="Copy link" permission="write" placement="menu" run={vi.fn()} />
    </FormView>, recordActionsMetadata(), undefined, formChildren({ "notes.Note#actions": {
      "notes.pause": { permission: "write", content: <button type="button">Pause</button> },
    } }));
    const trigger = await screen.findByRole("button", { name: "Actions" });
    const own = screen.getByRole("button", { name: "Sync now" });
    const contributed = screen.getByRole("button", { name: "Pause" });
    expect(own.compareDocumentPosition(contributed) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(contributed.compareDocumentPosition(trigger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(trigger.hasAttribute("data-overflow-trigger")).toBe(true);
  });

  test("an editable record keeps its command band's layout row from load to its first edit; a read-only record's collapses", async () => {
    const collapse = "not-has-[>div>:not([hidden]):not(:empty)]:hidden";
    const kept = "data-layout-band:not-has-[>div>:not([hidden]):not(:empty)]:invisible";
    const commandBand = () => screen.getByTestId("control-row").querySelector<HTMLElement>("[role=toolbar]");
    // As a record page renders it: the form's command band claims the layout's control row.
    function LayoutRow({ children }: { children: ReactElement }) {
      const [host, setHost] = useState<HTMLElement | null>(null);
      return <ControlBandProvider host={host}><div data-testid="control-row" ref={setHost} />{children}</ControlBandProvider>;
    }
    const renderNote = () => renderWithProviders(<LayoutRow><FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView></LayoutRow>);

    sdkMocks.record = { ...sdkMocks.record, permissions: ["read"] };
    renderNote();
    await screen.findByRole("heading", { name: "First" });
    expect(commandBand()?.classList).toContain(collapse);

    cleanup();
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read", "write"] };
    let resolveRecord!: (value: { data: Row }) => void;
    sdkMocks.getOne.mockImplementationOnce(() => new Promise((resolve) => { resolveRecord = resolve; }));
    renderNote();
    // The row is kept while the record loads, so its arrival shifts nothing either.
    await waitFor(() => expect(commandBand()?.classList).toContain(kept));
    expect(commandBand()?.classList).not.toContain(collapse);
    await act(async () => resolveRecord({ data: sdkMocks.record! }));
    await screen.findByDisplayValue("First");
    // Save and Discard arrive with the first edit into the row already kept for them.
    expect(commandBand()?.classList).toContain(kept);
    expect(commandBand()?.classList).not.toContain(collapse);
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

  test.each([false, true])("the form supplies %s archive state actions and selects their flags without page declarations", async (archived) => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: archived, is_trashed: false, permissions: ["read", "write", "delete"] };
    sdkMocks.projectToSelection = true;
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata());

    await screen.findByDisplayValue("First");
    expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining(["permissions", "is_archived", "is_trashed"]));
    expect(Boolean(screen.queryByText("Archived"))).toBe(archived);
    const label = archived ? "Unarchive" : "Archive";
    expect(screen.queryByRole("button", { name: label })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: label })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: archived ? "Archive" : "Unarchive" })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Move to trash" })).toBeTruthy();
    expect(screen.queryByRole("separator")).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: label }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { id: "note-1", is_archived: !archived } }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => expect(Boolean(screen.queryByText("Archived"))).toBe(!archived));
  });

  test.each(["explicit", "permission", "record-lock", "fields"] as const)("Archive follows the form's %s read-only rule", async (reason) => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, permissions: reason === "permission" ? ["read"] : ["read", "write", "delete"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly={reason === "explicit"}
      readOnlyWhen={reason === "record-lock" ? () => true : undefined}>
      <Field name="title" label="Title" title readOnly={reason === "fields"} />
      <Action id="inspect" label="Inspect" placement="menu" run={vi.fn()} />
    </FormView>, recordActionsMetadata());

    await screen.findByRole("heading", { name: "First" });
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Inspect" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Archive" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Unarchive" })).toBeNull();
    if (reason === "explicit") expect(screen.queryByRole("menuitem", { name: "Move to trash" })).toBeNull();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("the generic menu does not prevent a lone page verb from becoming a button", async () => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, is_trashed: false, permissions: ["write", "delete"] };
    const publish = vi.fn();
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Action id="publish" label="Publish" run={publish} />
    </FormView>, recordActionsMetadata());

    const button = await screen.findByRole("button", { name: "Publish" });
    expect(button.classList.contains("bg-inset")).toBe(true);
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Move to trash" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Publish" })).toBeNull();
  });

  test("page menu verbs precede containers, then Archive, Restore and Delete", async () => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, is_trashed: true, permissions: ["write", "delete"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      deleteAction={{ canDelete: true, isPending: false, onDelete: vi.fn() }}>
      <Field name="title" label="Title" title />
      <Action id="download" label="Download" placement="menu" run={vi.fn()} />
    </FormView>, recordActionsMetadata(), undefined, formChildren({
      "notes.Note#actions-menu": { "notes.export": { content: <ActionTrigger>Export</ActionTrigger> } },
    }));

    await screen.findByDisplayValue("First");
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    await screen.findByRole("menuitem", { name: "Archive" });
    const menu = within(screen.getByRole("menu"));
    expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Download", "Export", "Archive", "Restore", "Delete",
    ]);
    expect(menu.getAllByRole("separator")).toHaveLength(1);
  });

  test.each([false, true])("the form supplies the trash verb for is_trashed=%s even without an update root", async (trashed) => {
    sdkMocks.record = { ...sdkMocks.record, is_trashed: trashed, permissions: ["read", "delete"] };
    sdkMocks.projectToSelection = true;
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata({ roots: { update: undefined } }));

    await screen.findByRole("heading", { name: "First" });
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: trashed ? "Restore" : "Move to trash" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: trashed ? "Move to trash" : "Restore" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Archive" })).toBeNull();
    expect(sdkMocks.recordSelection).toContain("is_trashed");
  });

  test.each([undefined, null, [], ["read"], ["write"], ["delete"]].flatMap((permissions) =>
    [false, true].map((trashed) => ({ permissions, trashed }))))("record permissions gate writes and Trash, while Delete follows the resource and trash state ($permissions, trashed=$trashed)", async ({ permissions, trashed }) => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, is_trashed: trashed,
      ...(permissions === undefined ? {} : { permissions }) };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      deleteAction={{ canDelete: true, isPending: false, onDelete: vi.fn() }}>
      <Field name="title" label="Title" title />
      <Action id="inspect" label="Inspect" placement="menu" run={vi.fn()} />
    </FormView>, recordActionsMetadata());

    if (permissions?.includes("write")) await screen.findByDisplayValue("First");
    else await screen.findByRole("heading", { name: "First" });
    expect(Boolean(screen.queryByRole("textbox", { name: "Title" }))).toBe(Boolean(permissions?.includes("write")));
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    await screen.findByRole("menuitem", { name: "Inspect" });
    expect(Boolean(screen.queryByRole("menuitem", { name: "Archive" }))).toBe(Boolean(permissions?.includes("write")));
    for (const name of [trashed ? "Restore" : "Move to trash", "Delete"]) {
      const action = screen.queryByRole("menuitem", { name });
      expect(Boolean(action)).toBe(name === "Delete" ? trashed : Boolean(permissions?.includes("delete")));
      if (action) expect(action.getAttribute("aria-disabled")).not.toBe("true");
    }
  });

  test.each([false, true])("a form without permissions still gates Delete on trash state (%s)", async (trashed) => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, is_trashed: trashed };
    const metadata = recordActionsMetadata();
    const resource = metadata.labels["notes.Note"]!.resource;
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      deleteAction={{ canDelete: true, isPending: false, onDelete: vi.fn() }}>
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata({ fields: resource.fields?.filter((field) => field.name !== "permissions") }));
    await screen.findByDisplayValue("First");
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    for (const name of ["Archive", trashed ? "Restore" : "Move to trash"]) {
      expect(await screen.findByRole("menuitem", { name })).toBeTruthy();
    }
    expect(Boolean(screen.queryByRole("menuitem", { name: "Delete" }))).toBe(trashed);
  });

  test.each([undefined, null])("a missing trash state cannot offer hard Delete (%s)", async (is_trashed) => {
    sdkMocks.record = { ...sdkMocks.record, is_trashed, permissions: ["delete"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      deleteAction={{ canDelete: true, isPending: false, onDelete: vi.fn() }}>
      <Field name="title" label="Title" title />
      <Action id="inspect" label="Inspect" placement="menu" run={vi.fn()} />
    </FormView>, recordActionsMetadata());
    await screen.findByRole("heading", { name: "First" });
    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    await screen.findByRole("menuitem", { name: "Inspect" });
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
  });

  test.each([false, true])("a custom submit owns saves and receives no generic archive patch (%s)", async (archived) => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: archived, is_trashed: false, permissions: ["write", "delete"] };
    const submit = vi.fn(async () => ({ status: "ok" as const, data: sdkMocks.record! }));
    renderWithProviders(<FormView resource="notes.Note" id="note-1" submit={submit}>
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata());
    await screen.findByDisplayValue("First");
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Move to trash" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /^(Archive|Unarchive)$/ })).toBeNull();
    expect(submit).not.toHaveBeenCalled();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a dirty form disables Archive without sending a patch", async () => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, permissions: ["write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata());
    fireEvent.change(await screen.findByRole("textbox", { name: "Title" }), { target: { value: "Unsaved title" } });
    await screen.findByRole("button", { name: "Save" });
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    const archive = await screen.findByRole("menuitem", { name: "Archive" });
    expect(archive.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(archive);
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("Archive sends the saved expected_revision through the resource update", async () => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: false, revision: 7, permissions: ["write"] };
    const resource = recordActionsMetadata().labels["notes.Note"]!.resource;
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata({
      updateArguments: [{ name: "expected_revision", type: "Int" }],
      fields: [...(resource.fields ?? []), { name: "revision", kind: "scalar", scalar: "Int", readable: true,
        creatable: false, updatable: false, aggregatable: false, requiredOnCreate: false }],
    }));
    await screen.findByDisplayValue("First");
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Archive" }));
    await waitFor(() => expect(dataProvider.update).toHaveBeenCalledWith(expect.objectContaining({
      id: "note-1", variables: { is_archived: true },
      meta: expect.objectContaining({ gqlVariables: { expected_revision: 7 } }),
    })));
  });

  test("the form's trash prompt names its record title", async () => {
    sdkMocks.record = { ...sdkMocks.record, is_trashed: false, permissions: ["delete"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata());
    await screen.findByRole("heading", { name: "First" });
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Move to trash" }));
    const prompt = await screen.findByRole("alertdialog", { name: "Move “First” to the trash?" });
    fireEvent.click(within(prompt).getByRole("button", { name: "Keep “First”" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a create form has no generic record actions", () => {
    sdkMocks.record = null;
    renderWithProviders(<FormView resource="notes.Note">
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata());
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test.each(["document", "workspace"] as const)("an archived read-only record carries the chip in its %s header", async (recordPresentation) => {
    sdkMocks.record = { ...sdkMocks.record, is_archived: true, permissions: ["read"] };
    sdkMocks.projectToSelection = true;
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly recordPresentation={recordPresentation}>
      <Field name="title" label="Title" title />
    </FormView>, recordActionsMetadata());

    const title = await screen.findByRole("heading", { name: "First" });
    expect(within(title.closest("header")!).getByText("Archived")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
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

    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

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

    fireEvent.click(await screen.findByRole("button", { name: "Sync" }));

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

  test("labelled groups are titled sections of the sheet, never tabs", async () => {
    renderWithProviders(
      <Form resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Group label="Details">
          <Field name="summary" label="Summary" />
        </Group>
        <Group label="Schedule">
          <Field name="location" label="Location" />
        </Group>
      </Form>,
    );

    expect(await screen.findByLabelText("Title")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByRole("heading", { name: "Details" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Schedule" })).toBeTruthy();
    expect(screen.getByLabelText("Summary")).toBeTruthy();
    expect(screen.getByLabelText("Location")).toBeTruthy();
  });

  test("keeps collapsible groups closed in the sheet", async () => {
    renderWithProviders(<Form resource="notes.Note" id="note-1">
      <Field name="title" title />
      <Group label="Details" collapsible defaultOpen={false}><Field name="wordCount" /></Group>
      <Group label="Schedule"><Field name="reminderAt" /></Group>
    </Form>);
    expect(await screen.findByRole("heading", { name: "Schedule" })).toBeTruthy();
    const details = screen.getByRole("button", { name: "Details" });
    expect(details.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("true");
  });

  test("overflow sections remain reachable and keep the selected pane and unsaved form visible", async () => {
    const nativeRect = Element.prototype.getBoundingClientRect;
    const rect = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      if (this.querySelector(':scope > [role="tablist"]')) return { width: 310 } as DOMRect;
      if (this.parentElement?.hasAttribute("inert")) return { width: 100 } as DOMRect;
      return nativeRect.call(this);
    });
    try {
      renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
        recordTabs={["Overview", "Activity", "History", "Related"].map((label) => ({
          id: label, label, render: () => <p>{label} content</p>,
        }))} />);
      const title = await screen.findByLabelText("Title");
      fireEvent.change(title, { target: { value: "Unsaved title" } });
      expect(screen.queryByRole("tab", { name: "Related" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "More sections" }));
      fireEvent.click(await screen.findByRole("menuitem", { name: "Related" }));
      expect(await screen.findByRole("tab", { name: "Related", selected: true })).toBeTruthy();
      expect(screen.getByText("Related content")).toBeTruthy();
      expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Unsaved title");
      expect(screen.getAllByRole("tablist")).toHaveLength(1);
    } finally {
      rect.mockRestore();
    }
  });

  test.each(["document", "workspace"] as const)("%s records keep the sheet above one strip of declared and contributed panes", async (recordPresentation) => {
    const mounted = vi.fn();
    function RetainedPane({ active }: { active: boolean }): ReactElement {
      useEffect(() => { mounted(); }, []);
      return <output data-testid="body-retained-active">{String(active)}</output>;
    }
    renderWithProviders(<FormView resource="notes.Note" id="note-1" recordPresentation={recordPresentation}
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
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Editor", "Earlier2", "Later"]);
    expect(screen.getByRole("heading", { name: "Schedule" })).toBeTruthy();
    expect(screen.getByTestId("body-retained-active").textContent).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: /Earlier/ }));
    const contributedForm = await screen.findByRole("form", { name: "Contributed form" });
    expect(contributedForm.parentElement?.closest("form")).toBeNull();
    // The sheet stays whichever pane is open.
    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByText("Word Count")).toBeTruthy();
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    expect(screen.getByTestId("body-retained-active").textContent).toBe("false");
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    expect(screen.getByTestId("body-retained-active").textContent).toBe("true");
    expect(mounted).toHaveBeenCalledTimes(1);
  });

  test.each(["default", "controlled"])("%s pane selection opens a contributed id and reveal focuses a sheet field in place", async (selection) => {
    sdkMocks.record = { ...sdkMocks.record, location: "Office" };
    function Harness(): ReactElement {
      const [tab, setTab] = useState("pane");
      return <FormView resource="notes.Note" id="note-1" defaultRecordTab={selection === "default" ? "pane" : undefined}
        recordTab={selection === "controlled" ? tab : undefined} onRecordTabChange={setTab}
        recordTabs={[{ id: "activity", label: "Activity", render: ({ focusField }) =>
          <button type="button" onClick={() => focusField("location", selection === "default" ? { recordTabId: "missing" } : undefined)}>Reveal location</button> }]}>
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
    const location = screen.getByLabelText("Location");
    await waitFor(() => expect(document.activeElement).toBe(location));
    expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
  });

  test("a default-pane rule chooses from the loaded record once; the viewer's choice and a routed pane win", async () => {
    const rule = vi.fn((record: Row) => record.wordCount === 3 ? "activity" : undefined);
    // Counted in a component body, which runs only when the panel actually mounts.
    let fallbackRenders = 0;
    function NotesPane(): ReactElement { fallbackRenders += 1; return <p>Notes pane</p>; }
    const tabs = [
      { id: "notes", label: "Notes", render: () => <NotesPane /> },
      { id: "activity", label: "Activity", render: () => <p>Activity pane</p> },
    ];
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      defaultRecordTab={rule} recordTabs={tabs}>
      <Field name="title" title />
    </FormView>);
    expect(await screen.findByText("Activity pane")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Notes", "Activity"]);
    // The fallback (first) pane never rendered before the rule's choice.
    expect(fallbackRenders).toBe(0);
    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(await screen.findByText("Notes pane")).toBeTruthy();
    expect(rule).toHaveBeenCalledTimes(1);
    cleanup();

    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      defaultRecordTab={rule} recordTab="notes" recordTabs={tabs}>
      <Field name="title" title />
    </FormView>);
    expect(await screen.findByText("Notes pane")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Notes" }).getAttribute("aria-selected")).toBe("true");
  });

  test("create forms show no saved-record panes, whatever their default", async () => {
    sdkMocks.record = null;
    renderWithProviders(<FormView resource="notes.Note" defaultRecordTab="missing"
      recordTabs={[{ id: "pane", label: "Pane", render: () => <p>Saved panel</p> }]}>
      <Field name="title" label="Title" title />
      <Group label="Schedule"><Field name="location" label="Location" /></Group>
    </FormView>, undefined, undefined, formChildren({ "notes.Note#sections": {
      "notes.pane": { content: <Tab id="contributed" label="Contributed"><p>Saved contribution</p></Tab> },
    } }));
    expect(await screen.findByLabelText("Location")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByText("Saved panel")).toBeNull();
    expect(screen.queryByText("Saved contribution")).toBeNull();
  });

  test("one pane renders beneath the sheet under its heading, without a strip", async () => {
    const activeStates = vi.fn();
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
      recordTabs={[{ id: "preview", label: "Preview", badge: 2, keepMounted: true, render: ({ active }) => {
        activeStates(active);
        return <input aria-label="Preview draft" defaultValue="Original" />;
      } }]} />);
    const draft = await screen.findByRole("textbox", { name: "Preview draft" });
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("tabpanel")).toBeNull();
    expect(screen.getByRole("heading", { name: "Preview" }).parentElement?.textContent).toBe("Preview2");
    expect(activeStates).toHaveBeenLastCalledWith(true);
    // The sheet stays; the pane follows the form inside the record's one sheet background.
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    expect(screen.getByLabelText("Reminder").compareDocumentPosition(draft) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(draft.closest("form")).toBeNull();
    expect(draft.closest(".bg-sheet")?.className).toContain("min-h-full");
    expect(document.querySelector("form")?.className ?? "").not.toContain("min-h-full");
    // A retained draft survives the record re-rendering around it.
    fireEvent.change(draft, { target: { value: "Unsaved draft" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Renamed" } });
    expect(await screen.findByRole("button", { name: "Save" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Preview draft" })).toBe(draft);
    expect(draft).toHaveProperty("value", "Unsaved draft");
  });

  test("a pane group follows the saved-record tabs in its own pane, bound to the record's form", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      recordTabs={[{ id: "content", label: "Content", render: () => <p>Content panel</p> }]}>
      <Field name="title" label="Title" title />
      <Group label="Envelope" pane="envelope"><Field name="location" label="Location" /></Group>
    </FormView>);
    // The saved-record tab leads and opens by default; the group's fields are not in the sheet.
    expect(await screen.findByText("Content panel")).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Content", "Envelope"]);
    expect(screen.queryByLabelText("Location")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Envelope" }));
    // The strip names the group: its fields sit in the pane under no second heading.
    const location = await screen.findByLabelText("Location");
    expect(location.closest("[role='tabpanel']")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Envelope" })).toBeNull();
    fireEvent.change(location, { target: { value: "Lisbon" } });
    expect(await screen.findByRole("button", { name: "Save" })).toBeTruthy();
  });

  test("a pane group's content reads the record chrome like the sheet's sections", async () => {
    function RecordProbe(): ReactElement {
      const chrome = useRecordChromeContext();
      return <span data-testid="pane-record">{chrome.resource}:{chrome.recordId}:{String(chrome.record?.title ?? "")}</span>;
    }
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Group label="Details"><Field name="wordCount" /></Group>
      <Group label="Audit" pane="audit" content={<RecordProbe />} />
    </FormView>);
    await screen.findByLabelText("Title");
    // A lone pane renders beneath the sheet under its heading; its content sees the saved record.
    expect((await screen.findByTestId("pane-record")).textContent).toBe(`notes.Note:note-1:${String(sdkMocks.record?.title)}`);
  });

  test("rejects a pane group whose id collides with a record tab", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => renderWithProviders(<FormView resource="notes.Note" id="note-1"
        recordTabs={[{ id: "envelope", label: "Envelope", render: () => null }]}>
        <Field name="title" label="Title" title />
        <Group label="Envelope" pane="envelope"><Field name="location" label="Location" /></Group>
      </FormView>)).toThrow(/duplicate record tab id "envelope"/);
    } finally {
      consoleError.mockRestore();
    }
  });

  test("a full-bleed pane fills the height beneath the sheet, which scrolls in its own region", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
      recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, presentation: "full-bleed",
        render: () => <div data-testid="lone-canvas" className="min-h-0 flex-1">Canvas</div> }]} />);
    const canvas = await screen.findByTestId("lone-canvas");
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByRole("heading", { name: "Editor" })).toBeTruthy();
    const panel = canvas.parentElement!;
    expect(panel.className).toContain("flex-1");
    // The pane is a flex column, so a `flex-1` canvas fills it.
    expect(panel.className).toContain("flex-col");
    expect(panel.className).toContain("overflow-hidden");
    expect(panel.className).not.toContain("max-w-[1100px]");
    expect(panel.parentElement?.className).toContain("h-full");
    expect(canvas.closest("form")).toBeNull();
    const reminder = screen.getByLabelText("Reminder");
    expect(reminder.closest(".overflow-auto")?.className).toContain("max-h-[50%]");
  });

  test("a workspace record scrolls its sheet and panes together under its compact header", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} recordPresentation="workspace"
      recordTabs={[{ id: "editor", label: "Editor", render: () => <p data-testid="workspace-pane">Pane</p> }]} />);
    const pane = await screen.findByTestId("workspace-pane");
    const title = screen.getByRole("textbox", { name: "Title" });
    expect(title.className).not.toContain("text-28");
    expect(title.closest("header")?.parentElement?.className).toContain("sticky");
    const root = pane.closest(".bg-sheet")!;
    expect(root.className).toContain("overflow-auto");
    expect(root.contains(screen.getByLabelText("Reminder"))).toBe(true);
    expect(pane.closest(".max-w-\\[1100px\\]")).toBeTruthy();
  });

  test.each([false, true])("a routed pane hidden by visibleWhen falls back to the first pane (second visible: %s)", async (secondVisible) => {
    sdkMocks.record = { ...sdkMocks.record, wordCount: secondVisible ? 5 : 3 };
    const onRecordTabChange = vi.fn();
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
      recordTab="activity" onRecordTabChange={onRecordTabChange}
      recordTabs={[
        { id: "preview", label: "Preview", render: ({ active }) => <p>Preview panel {String(active)}</p> },
        { id: "activity", label: "Activity", visibleWhen: (record) => record.wordCount !== 3,
          render: () => <p>Activity panel</p> },
      ]} />);
    await screen.findByDisplayValue("First");
    if (secondVisible) {
      // Two visible panes keep the strip and the routed pane.
      expect(await screen.findByText("Activity panel")).toBeTruthy();
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Preview", "Activity"]);
      expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
      expect(screen.queryByRole("heading", { name: "Preview" })).toBeNull();
    } else {
      expect(await screen.findByText("Preview panel true")).toBeTruthy();
      expect(screen.queryByRole("tablist")).toBeNull();
      expect(screen.getByRole("heading", { name: "Preview" })).toBeTruthy();
      expect(screen.queryByText("Activity panel")).toBeNull();
    }
    expect(onRecordTabChange).not.toHaveBeenCalled();
  });

  test.each(["overview", "unknown"])("a routed %s id names no pane: the first pane opens and the URL is left alone", async (routed) => {
    const onRecordTabChange = vi.fn();
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
      recordTab={routed} onRecordTabChange={onRecordTabChange}
      recordTabs={[
        { id: "notes", label: "Notes", render: () => <p>Notes pane</p> },
        { id: "activity", label: "Activity", render: () => <p>Activity pane</p> },
      ]} />);
    expect(await screen.findByText("Notes pane")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Notes" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    expect(onRecordTabChange).not.toHaveBeenCalled();
  });

  test("rejects a record tab claiming the editable lines' pane id", () => {
    expect(() => renderWithProviders(<FormView resource="notes.Note" id="note-1"
      recordTabs={[{ id: "editable-lines", label: "Record", render: () => null }]} />)).toThrow(/duplicate record tab id "editable-lines"/);
  });

  test("locks the saved form when projected permissions omit write", async () => {
    sdkMocks.record = { ...sdkMocks.record, permissions: ["read"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "title", title: true }]} />, recordActionsMetadata());
    expect(await screen.findByRole("heading", { name: "First" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  });

  test("shows a lock and label without a value for actor-redacted fields", async () => {
    sdkMocks.record = {
      id: "note-1",
      title: "Visible title",
      secret: null,
      redacted_fields: ["secret"],
      permissions: ["read"],
    };
    const note = defaultModel("NoteType", "notes.Note");
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" fields={[
        { name: "title", title: true },
        { name: "secret", label: "Private note" },
      ]} />,
      { types: { NoteType: { ...note, fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
        secret: { name: "secret", kind: "scalar", scalar: "String" },
        redacted_fields: { name: "redacted_fields", kind: "list", scalar: "String" },
        permissions: { name: "permissions", kind: "list", scalar: "String" },
      } } } },
    );

    expect(await screen.findByRole("heading", { name: "Visible title" })).toBeTruthy();
    expect(screen.getByText("Private note")).toBeTruthy();
    expect(screen.getByLabelText("Restricted: Private note")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Private note" })).toBeNull();
    expect(sdkMocks.recordSelection).toContain("redacted_fields");
  });

  test("keeps a redacted context fact visible as its label and lock", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = {
      id: "note-1",
      title: "Visible title",
      secret: null,
      redacted_fields: ["secret"],
    };
    const note = defaultModel("NoteType", "notes.Note");
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" readOnly fields={[{ name: "title", title: true }]} />,
      { types: { NoteType: {
        ...note,
        fields: {
          title: { name: "title", kind: "scalar", scalar: "String" },
          secret: { name: "secret", label: "Private context", kind: "scalar", scalar: "String" },
          redacted_fields: { name: "redacted_fields", kind: "list", scalar: "String" },
        },
        resource: { ...note.resource, contextFields: ["secret"] },
      } } },
    );

    expect(await screen.findByRole("heading", { name: "Visible title" })).toBeTruthy();
    expect(screen.getByText("Private context")).toBeTruthy();
    expect(screen.getByLabelText("Restricted: Private context")).toBeTruthy();
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

  test("a missing record renders the form's renderUnavailable with its id and the default state", () => {
    const renderUnavailable = vi.fn((id: string, unavailable: ReactElement) => <div>
      <p>{`Moved: ${id}`}</p>
      {unavailable}
    </div>);
    renderWithProviders(<FormView resource="notes.Note" id="missing"
      fields={[{ name: "title", title: true }]}
      acknowledgedSource={{ record: null, values: null, loading: false }}
      renderUnavailable={renderUnavailable} />);
    expect(screen.getByText("Moved: missing")).toBeTruthy();
    expect(screen.getByText("Record unavailable")).toBeTruthy();
    expect(renderUnavailable).toHaveBeenCalledWith("missing", expect.anything());
  });

  test("a renderUnavailable that returns the element it was passed renders the default unchanged", () => {
    renderWithProviders(<FormView resource="notes.Note" id="missing"
      fields={[{ name: "title", title: true }]}
      acknowledgedSource={{ record: null, values: null, loading: false }}
      renderUnavailable={(_id, unavailable) => unavailable} />);
    expect(screen.getByText("Record unavailable")).toBeTruthy();
  });

  test("renderUnavailable is not called while loading, on a load error, or for a new record", async () => {
    const renderUnavailable = vi.fn(() => <p>Moved</p>);
    renderWithProviders(<FormView resource="notes.Note" id="loading"
      fields={[{ name: "title", title: true }]}
      acknowledgedSource={{ record: null, values: null, loading: true }}
      renderUnavailable={renderUnavailable} />);
    expect(screen.queryByText("Moved")).toBeNull();
    cleanup();

    sdkMocks.record = null;
    renderWithProviders(<FormView resource="notes.Note" id="failing"
      fields={[{ name: "title", title: true }]}
      renderUnavailable={renderUnavailable} />);
    expect(await screen.findByText("Could not load this record.")).toBeTruthy();
    expect(screen.queryByText("Moved")).toBeNull();
    cleanup();

    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "title", title: true }]}
      renderUnavailable={renderUnavailable} />);
    expect(screen.queryByText("Moved")).toBeNull();
    expect(screen.queryByText("Record unavailable")).toBeNull();
    expect(renderUnavailable).not.toHaveBeenCalled();
  });

  test("lets a long saved title wrap in the hero", async () => {
    sdkMocks.record = { ...sdkMocks.record, title: "A long record title with several words that need wrapping" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly fields={[{ name: "title", title: true }]} />);
    const heading = await screen.findByRole("heading", { name: String(sdkMocks.record.title) });
    expect(heading.className).toContain("overflow-wrap:anywhere");
    expect(heading.className).not.toContain("truncate");
  });

  test("renders metadata context fields with native widgets and readable attribution", async () => {
    window.sessionStorage.setItem("angee:developer-mode", "0");
    sdkMocks.projectToSelection = true;
    sdkMocks.record = {
      id: "note-1",
      title: "Invoice 104",
      amount: "1234.50",
      currency: { id: "cur_usd", code: "USD" },
      due_on: "2026-10-31",
      created_by: "user_ada",
      created_by_label: "Ada",
      updated_by: "user_grace",
      updated_by_label: "Grace",
      created_at: "2026-10-01T10:11:12Z",
      updated_at: "2026-10-18T13:14:15Z",
      permissions: ["read"],
    };
    const money = {
      read: ({ value, row, field }: { value?: unknown; row?: unknown; field?: { currencyField?: string } }) => {
        const currency = (row as { currency?: { code?: string } } | undefined)?.currency?.code;
        return <span>{String(value)} {currency ?? field?.currencyField}</span>;
      },
    };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" readOnly fields={[{ name: "title", title: true }]} />,
      factsMetadata(),
      undefined,
      { widgets: { ...defaultWidgets, money } },
    );

    expect(await screen.findByRole("heading", { name: "Invoice 104" })).toBeTruthy();
    expect(screen.getByText("1234.50 USD")).toBeTruthy();
    expect(screen.getByText(/Oct 31, 2026/)).toBeTruthy();
    expect(screen.getByText("created by").parentElement?.textContent).toContain("Ada");
    expect(screen.getByText("updated by").parentElement?.textContent).toContain("Grace");
    expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining([
      "amount", "currency.id", "currency.code", "due_on",
      "created_by_label", "updated_by_label",
    ]));
    expect(screen.queryByText("note-1")).toBeNull();
    expect(screen.queryByText("2026-10-01T10:11:12Z")).toBeNull();
  });

  test("guard: a date in the facts line carries its label, where an amount stands bare", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "note-1", title: "Invoice 104", amount: "1234.50", currency: { id: "cur_usd", code: "USD" },
      due_on: "2026-10-31", permissions: ["read"] };
    const money = { read: ({ value }: { value?: unknown }) => <span>{String(value)}</span> };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" readOnly fields={[{ name: "title", title: true }]} />,
      factsMetadata(),
      undefined,
      { widgets: { ...defaultWidgets, money } },
    );
    const date = await screen.findByText(/Oct 31, 2026/);
    // "due on Oct 31": the date's own span holds its label, lowercased by the facts line.
    expect(date.closest("span.inline-flex")?.textContent).toMatch(/^due on/i);
    expect(screen.getByText("1234.50").closest("span.inline-flex")).toBeNull();
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
    // The rail sits beside the sheet, above the record's pane.
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

  test("a rail field shows exactly when the sheet's visibility rule shows it for the form's values", async () => {
    const scalar = (name: string) => ({ name, kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false });
    const metadataFields = ["kind", "priority", "estimate", "owner"].map(scalar);
    const resource = testDataResource("notes.Note", { fields: metadataFields });
    const record = { id: "note-1", title: "First", kind: "note", priority: "High", estimate: "3", owner: "Ada",
      permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      fields={[{ name: "title", title: true }, { name: "kind", label: "Kind" }]}
      acknowledgedSource={{ record, values: record }} />,
    { types: { NoteType: { resource, fields: Object.fromEntries(metadataFields.map((field) => [field.name, field])) } } },
    undefined,
    formChildren({ "notes.Note#rail": { "notes.properties": {
      content: <FormView.RailGroup id="properties" label="Properties" fields={[
        { field: { name: "priority", showWhen: (values) => values.kind === "task" } },
        { field: { name: "estimate", hidden: true } },
        { field: { name: "owner" } },
      ]} /> } } }));
    const rail = await screen.findByRole("complementary");
    const railControl = (name: string) => within(rail).queryByRole("textbox", { name });
    const kind = screen.getByRole("textbox", { name: "Kind" });
    // A body value that fails the condition hides the field; a hidden field never shows; the rest stays.
    expect(railControl("Owner")).toBeTruthy();
    expect(railControl("Priority")).toBeNull();
    expect(railControl("Estimate")).toBeNull();

    fireEvent.change(kind, { target: { value: "task" } });
    expect((await within(rail).findByRole("textbox", { name: "Priority" }) as HTMLInputElement).value).toBe("High");
    expect(railControl("Estimate")).toBeNull();
    expect(railControl("Owner")).toBeTruthy();

    fireEvent.change(kind, { target: { value: "note" } });
    await waitFor(() => expect(railControl("Priority")).toBeNull());
    expect(railControl("Estimate")).toBeNull();
    expect(railControl("Owner")).toBeTruthy();
  });

  test("a rail field the sheet also declares shows by the sheet's visibility rule, presented as the rail declares it", async () => {
    const scalar = (name: string) => ({ name, kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false });
    const metadataFields = ["kind", "priority", "owner"].map(scalar);
    const resource = testDataResource("notes.Note", { fields: metadataFields });
    const record = { id: "note-1", title: "First", kind: "note", priority: "High", owner: "Ada", permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      fields={[
        { name: "title", title: true }, { name: "kind", label: "Kind" },
        { name: "priority", label: "Priority level", showWhen: (values) => values.kind === "task" },
      ]}
      acknowledgedSource={{ record, values: record }} />,
    { types: { NoteType: { resource, fields: Object.fromEntries(metadataFields.map((field) => [field.name, field])) } } },
    undefined,
    formChildren({ "notes.Note#rail": { "notes.properties": {
      content: <FormView.RailGroup id="properties" label="Properties" fields={[
        { field: { name: "priority", label: "Priority" } },
        { field: { name: "owner" } },
      ]} /> } } }));
    const rail = await screen.findByRole("complementary");
    const railControl = (name: string) => within(rail).queryByRole("textbox", { name });
    const kind = screen.getByRole("textbox", { name: "Kind" });
    // The sheet hides its priority for a note, so the rail does too; a field only the rail declares stays.
    expect(railControl("Priority")).toBeNull();
    expect(railControl("Owner")).toBeTruthy();

    fireEvent.change(kind, { target: { value: "task" } });
    expect((await within(rail).findByRole("textbox", { name: "Priority" }) as HTMLInputElement).value).toBe("High");
    expect(railControl("Owner")).toBeTruthy();

    fireEvent.change(kind, { target: { value: "note" } });
    await waitFor(() => expect(railControl("Priority")).toBeNull());
    expect(railControl("Owner")).toBeTruthy();
  });

  test("a field two rail groups name shows by each row's own rule, never by the other row's", async () => {
    const priority = { name: "priority", kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false };
    const resource = testDataResource("notes.Note", { fields: [priority] });
    const record = { id: "note-1", title: "First", priority: "High", permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "title", title: true }]}
      acknowledgedSource={{ record, values: record }} />,
    { types: { NoteType: { resource, fields: { priority } } } }, undefined,
    formChildren({ "notes.Note#rail": {
      "notes.hidden": { content: <FormView.RailGroup id="hidden" label="Hidden"
        fields={[{ field: { name: "priority", label: "Priority", showWhen: () => false } }]} /> },
      "notes.shown": { content: <FormView.RailGroup id="shown" label="Shown"
        fields={[{ field: { name: "priority", label: "Priority" } }]} /> },
    } }));
    const rail = await screen.findByRole("complementary");
    expect(within(rail).queryByText("Hidden")).toBeNull();
    expect(within(rail).getByText("Shown")).toBeTruthy();
    expect((within(rail).getByRole("textbox", { name: "Priority" }) as HTMLInputElement).value).toBe("High");
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
      expect(screen.getByRole("combobox", { name: /Reviewer/ }).textContent).toContain(
        "Reviewer One",
      ),
    );
    fireEvent.click(screen.getByRole("combobox", { name: /Reviewer/ }));
    fireEvent.click(await screen.findByText("Reviewer Two"));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /Reviewer/ }).textContent).toContain(
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
              update: "update_inference_providers_by_pk",
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
        screen.getByRole("combobox", { name: "Reviewer: Primary Reviewer" }),
      ).toBeTruthy(),
    );
    expect(sdkMocks.getList).not.toHaveBeenCalled();
    expect(sdkMocks.recordSelection).toContain("reviewer");
    expect(sdkMocks.recordSelection).not.toContain("reviewer.id");
    expect(sdkMocks.recordSelection).not.toContain("reviewer.display_name");

    // Opening the picker fires the option list once; its fresh label then wins.
    fireEvent.click(
      screen.getByRole("combobox", { name: "Reviewer: Primary Reviewer" }),
    );
    await waitFor(() => expect(sdkMocks.getList).toHaveBeenCalledWith(
      expect.objectContaining({
        resource: "reviewers",
        filters: [{ field: "company", operator: "eq", value: "cmp_1" }],
      }),
    ));
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Reviewer: Reviewer From List" }),
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
    expect(screen.queryByRole("combobox", { name: /Workflow/ })).toBeNull();
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

  test("edits a to-many relation as chips and saves the picked id list", async () => {
    sdkMocks.projectToSelection = true;
    sdkMocks.record = { id: "team-1", name: "Core", members: [{ id: "user-1", display_name: "Ada" }] };
    sdkMocks.listRows = [{ id: "user-1", display_name: "Ada" }, { id: "user-2", display_name: "Grace" }];
    renderWithProviders(<FormView resource="teams.Team" id="team-1" fields={[
      { name: "name", title: true }, { name: "members", label: "Members" },
    ]} />, teamMetadata());

    expect(await screen.findByRole("button", { name: "Remove Ada" })).toBeTruthy();
    expect(sdkMocks.recordSelection).toEqual(expect.arrayContaining(["members.id", "members.display_name"]));
    expect(screen.queryByRole("textbox", { name: "Members" })).toBeNull();
    // One chips field: create is the search's last option, not a button beside it.
    expect(screen.queryByRole("button", { name: "New user" })).toBeNull();
    await pickRelated("Members", "Grace");
    expect(await screen.findByRole("button", { name: "Remove Grace" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { id: "team-1", members: ["user-1", "user-2"] },
    }));
  });

  test("re-picking a to-many relation's saved records leaves the form clean", async () => {
    sdkMocks.record = { id: "team-1", name: "Core", members: [
      { id: "user-1", display_name: "Ada" }, { id: "user-2", display_name: "Grace" },
    ] };
    sdkMocks.listRows = [{ id: "user-1", display_name: "Ada" }, { id: "user-2", display_name: "Grace" }];
    renderWithProviders(<FormView resource="teams.Team" id="team-1" fields={[
      { name: "name", title: true }, { name: "members", label: "Members" },
    ]} />, teamMetadata());

    fireEvent.click(await screen.findByRole("button", { name: "Remove Ada" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    await pickRelated("Members", "Ada");

    await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
    expect(screen.getAllByRole("button", { name: /^Remove / }).map((button) => button.getAttribute("aria-label")))
      .toEqual(["Remove Ada", "Remove Grace"]);
  });

  test("a create form submits a to-many relation as its picked id list", async () => {
    sdkMocks.record = null;
    sdkMocks.listRows = [{ id: "user-1", display_name: "Ada" }];
    renderWithProviders(<FormView resource="teams.Team" id={null} fields={[
      { name: "name", title: true }, { name: "members", label: "Members" },
    ]} />, teamMetadata());

    await pickRelated("Members", "Ada");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: expect.objectContaining({ members: ["user-1"] }),
    }));
  });

  test("a read-only to-many relation renders linked chips without listing options", async () => {
    sdkMocks.record = { id: "team-1", name: "Core", members: [{ id: "user-1", display_name: "Ada" }] };
    renderWithProviders(<FormView resource="teams.Team" id="team-1" readOnly fields={[
      { name: "name", title: true }, { name: "members", label: "Members" },
    ]} />, teamMetadata(), undefined, {
      routeHref: createRouteHref([{ name: "users", path: "/users" }, { name: "users.record", path: "/users/$id" }]),
      routesByResource: { "iam.User": { collection: "users", record: { name: "users.record", param: "id" } } },
    });

    expect((await screen.findByRole("link", { name: "Ada" })).getAttribute("href")).toBe("/users/user-1");
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove|New user/ })).toBeNull();
    expect(sdkMocks.getList).not.toHaveBeenCalled();
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

    const picker = await screen.findByRole("combobox", {
      name: "Workflow: Daily briefing",
    });
    expect(screen.queryByRole("textbox", { name: "Workflow" })).toBeNull();
    fireEvent.click(picker);
    fireEvent.click(await screen.findByText("Weekly review"));
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Workflow: Weekly review" }),
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
    expect(screen.getByRole("combobox", { name: "Workflow" }).textContent).toContain(
      "Select…",
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

  test("a config slug longer than its limit says so, a cleared one says the field is required, and correcting it clears the message", async () => {
    sdkMocks.mutate.mockClear();
    const descriptors = deserializeFormSpec({
      type: "object", required: ["slug"],
      properties: { slug: { type: "string", label: "Slug", minLength: 1, maxLength: 64, widget: "slug" } },
    }, defaultWidgets).map((field) => ({ ...field, name: `target_config.${field.name}` }));
    renderWithProviders(
      <FormView resource="OAuthClient" fields={descriptors} defaultValues={{ target_config: { slug: "a".repeat(65) } }} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Enter no more than 64 characters.")).toBeTruthy();
    expect(screen.queryByText("This field is required.")).toBeNull();
    const slug = screen.getByRole("textbox", { name: "Slug" });
    fireEvent.change(slug, { target: { value: "" } });
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.queryByText(/Enter (at least|no more than)/)).toBeNull();
    fireEvent.change(slug, { target: { value: "front-desk" } });
    await waitFor(() => expect(screen.queryByText("This field is required.")).toBeNull());
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("an empty text under a minimum length of one is required unless the field is omittable, and keeps the length message under a minimum above one", async () => {
    sdkMocks.mutate.mockClear();
    const descriptors = deserializeFormSpec({ type: "object", properties: {
      slug: { type: "string", label: "Slug", minLength: 1, maxLength: 64 },
      nick: { type: "string", label: "Nick", minLength: 1, omittable: true },
      handle: { type: "string", label: "Handle", minLength: 3 },
    } }, defaultWidgets);
    renderWithProviders(<FormView resource="OAuthClient" fields={descriptors} defaultValues={{ slug: "", nick: "", handle: "" }} />);

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Enter at least 3 characters.");
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent;
    expect(messageUnder("Slug")).toContain("This field is required.");
    expect(messageUnder("Nick")).toContain("Enter at least 1 character.");
    expect(messageUnder("Nick")).not.toContain("This field is required.");
    expect(messageUnder("Handle")).not.toContain("This field is required.");
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a cleared nullable text under a minimum length of one keeps the length message, and a cleared required one says the field is required", async () => {
    sdkMocks.record = { id: "note-1", caption: "hi", slug: "front-desk" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={deserializeFormSpec({ type: "object", properties: {
      caption: { type: "string", label: "Caption", minLength: 1, nullable: true },
      slug: { type: "string", label: "Slug", minLength: 1 },
    } }, defaultWidgets)} />);
    const caption = await screen.findByRole("textbox", { name: "Caption" });
    await waitFor(() => expect((caption as HTMLInputElement).value).toBe("hi"));
    fireEvent.change(caption, { target: { value: "" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Slug" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Enter at least 1 character.");
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent;
    expect(messageUnder("Caption")).toContain("Enter at least 1 character.");
    expect(messageUnder("Caption")).not.toContain("This field is required.");
    expect(messageUnder("Slug")).toContain("This field is required.");
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("each value rule a record form checks shows its own message, and a missing value says the field is required", async () => {
    sdkMocks.mutate.mockClear();
    const descriptors = deserializeFormSpec({ type: "object", properties: {
      ...ruleSchemaProperties,
      token: { type: "string", label: "Token", presenceRequired: true },
      remark: { type: "string", label: "Remark", presenceRequired: true },
    } }, defaultWidgets);
    renderWithProviders(<FormView resource="OAuthClient" fields={descriptors} defaultValues={{ ...ruleValues, remark: null }} />);

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText(ruleMessages.Handle);
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent;
    for (const [label, message] of Object.entries({
      ...ruleMessages, Token: "This field is required.", Remark: "This field is required.",
    })) {
      expect(messageUnder(label)).toContain(message);
    }
    expect(screen.getAllByText("This field is required.")).toHaveLength(2);
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // A FormSpec object whose `required` names a member: the member is `required`, without a presence flag.
  const authProperties = (token: Readonly<Record<string, unknown>>) => ({
    auth: { type: "object", widget: "object", label: "Auth", required: ["token"], properties: {
      token: { label: "Token", ...token }, note: { type: "string", label: "Note" },
    } },
  });
  test("a required member absent is refused with its message under it, Create enabled before the first submit; filled, it saves with the member", async () => {
    sdkMocks.mutate.mockClear();
    const requiredMessage = "This field is required.";
    renderWithProviders(<FormView resource="OAuthClient"
      fields={deserializeFormSpec({ type: "object", properties: authProperties({ type: "string" }) }, defaultWidgets)} />);
    const token = await screen.findByLabelText("Token") as HTMLInputElement;
    const create = screen.getByRole<HTMLButtonElement>("button", { name: "Create" });
    expect(create.disabled).toBe(false);

    fireEvent.click(create);
    expect(await screen.findByText(requiredMessage)).toBeTruthy();
    expect(screen.getByText("Token").closest('[data-layout="stack"]')?.textContent).toContain(requiredMessage);
    expect(token.getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();

    fireEvent.change(token, { target: { value: "abc" } });
    fireEvent.blur(token);
    await waitFor(() => expect(screen.queryByText(requiredMessage)).toBeNull());
    fireEvent.click(create);
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ auth: { token: "abc" } });
  });

  test("a required member holding a blank text is a value: Create saves it as held, with no message", async () => {
    sdkMocks.mutate.mockClear();
    renderWithProviders(<FormView resource="OAuthClient" defaultValues={{ auth: { token: " " } }}
      fields={deserializeFormSpec({ type: "object", properties: authProperties({ type: "string" }) }, defaultWidgets)} />);
    await screen.findByLabelText("Token");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ auth: { token: " " } });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test("guard: a member that is required and nullable, holding null, is accepted and saved", async () => {
    sdkMocks.mutate.mockClear();
    renderWithProviders(<FormView resource="OAuthClient" defaultValues={{ auth: { token: null, note: "n" } }}
      fields={deserializeFormSpec({ type: "object", properties: authProperties({ anyOf: [{ type: "string" }, { type: "null" }] }) }, defaultWidgets)} />);
    await screen.findByLabelText("Note");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ auth: { token: null, note: "n" } });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test("guard: a required number left empty, holding null, says the field is required, never that it wants another type, and is not saved", async () => {
    sdkMocks.mutate.mockClear();
    renderWithProviders(<FormView resource="OAuthClient" fields={deserializeFormSpec({ type: "object", required: ["count"],
      properties: { count: { type: "integer", label: "Count" } } }, defaultWidgets)} />);
    await screen.findByLabelText("Count");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(screen.getByText("Count").closest('[data-layout="stack"]')?.textContent).toContain("This field is required."));
    expect(screen.queryByText(/^Enter a valid/)).toBeNull();
    // The message under the field is the check's answer to this submit, which it refused; React Hook
    // Form calls the save only when the check passes, so the save never started and a slow one cannot
    // land after this assertion.
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("guard: a create leaves out an untouched date carried as text, though it holds an empty text, as it leaves out any date or time left empty", async () => {
    sdkMocks.mutate.mockClear();
    renderWithProviders(<FormView resource="OAuthClient" fields={deserializeFormSpec({ type: "object", properties: {
      label: { type: "string", label: "Label" }, day: { type: "string", widget: "date", label: "Day" },
    } }, defaultWidgets)} />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Label" }), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
  });

  test("guard: a create leaves an untouched optional member out of what it saves", async () => {
    sdkMocks.mutate.mockClear();
    renderWithProviders(<FormView resource="OAuthClient" fields={deserializeFormSpec({ type: "object", properties: {
      label: { type: "string", label: "Label" }, count: { type: "integer", label: "Count" },
    } }, defaultWidgets)} />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Label" }), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
  });

  // A date carried as text declares its format beside its date widget, as the backend's form spec emits
  // it: the format, not the widget, makes its empty value null. The backend also marks every member
  // omittable or presence-required; the edit and draft tests below use members without those marks, as
  // a hand-written or raw schema declares them, which is where those seeds changed.
  test("a date carried as text, holding null, offers no Set value: its declared format makes its empty value null", async () => {
    sdkMocks.record = { id: "note-1", title: "First", due: null };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={deserializeFormSpec({ type: "object", properties: {
      due: { anyOf: [{ type: "string", format: "date", widget: "date" }, { type: "null" }], label: "Due" },
    } }, defaultWidgets)} />);
    await screen.findByText("Left empty");
    expect(screen.queryByRole("button", { name: "Set value" })).toBeNull();
  });

  test("a date carried as text that the record lacks is sent as null when an edit writes its object", async () => {
    const members = deserializeFormSpec({ type: "object", properties: {
      name: { type: "string", label: "Name" }, day: { type: "string", format: "date", widget: "date", label: "Day" },
    } }, defaultWidgets).map((field) => ({ ...field, name: `config.${field.name}` }));
    sdkMocks.record = { id: "note-1", title: "First", config: { name: "a" } };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={members} />);
    const name = await screen.findByRole("textbox", { name: "Name" }) as HTMLInputElement;
    await waitFor(() => expect(name.value).toBe("a"));
    fireEvent.change(name, { target: { value: "b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data.config.day).toBeNull();
  });

  test.each<[string, Record<string, unknown>, Record<string, unknown>]>([
    ["nullable, holds null", { anyOf: [{ type: "string", format: "date", widget: "date" }, { type: "null" }] }, { name: "", day: null }],
    ["neither required nor nullable, is left out, as its type refuses null", { type: "string", format: "date", widget: "date" }, { name: "" }],
  ])("a create draft's date carried as text that is %s", (_case, day, draft) => {
    const members = deserializeFormSpec({ type: "object", properties: { name: { type: "string", label: "Name" }, day } }, defaultWidgets)
      .map((field) => ({ ...field, name: `config.${field.name}` }));
    expect(emptyDraft(members)).toStrictEqual({ config: draft });
  });

  const labelField = { name: "label", label: "Label", required: true } satisfies FormField;
  test.each<[string, FormField, string | undefined]>([
    ["its own readOnly flag, on a create", { name: "code", label: "Code", required: true, readOnly: true }, undefined],
    ["a resolve that makes it read-only, on a create", { name: "code", label: "Code", required: true, resolve: () => ({ readOnly: true }) }, undefined],
    ["editOnly, on a create", { name: "code", label: "Code", required: true, editOnly: true }, undefined],
    ["createOnly, on an edit", { name: "code", label: "Code", required: true, createOnly: true }, "note-1"],
    ["its own readOnly flag, on an edit", { name: "code", label: "Code", required: true, readOnly: true }, "note-1"],
  ])("a required field the person cannot change, by %s, left empty, does not block the save and shows no message", async (_cause, code, id) => {
    sdkMocks.record = { id: "note-1", label: "First", code: "" };
    renderWithProviders(<FormView resource="notes.Note" id={id ?? null} fields={[labelField, code]} />);
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    if (id) await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: id ? "Save" : "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual(id ? { id, label: "Second" } : { label: "Second" });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test("guard: the same required field, editable and left empty, blocks the save with its message under it", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code", required: true }]} />);
    const label = await screen.findByLabelText("Label");
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByText("Code").closest('[data-layout="stack"]')?.textContent).toContain("This field is required.");
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // A model whose create metadata requires `code`.
  const codeRequiredOnCreate: TestSchemaMetadata = { types: { NoteType: {
    ...defaultModel("NoteType", "notes.Note"),
    resource: { ...defaultResource("NoteType", "notes.Note"), requiredCreateFields: ["code"] },
  } } };
  // Each left empty as a create drafts it, with what a save sends when nothing stops it.
  const requiredCodes: readonly [string, FormField, TestSchemaMetadata | undefined, Row][] = [
    ["a plain field required by its own flag", { name: "code", label: "Code", required: true }, undefined, { label: "Second", code: "" }],
    [
      "a structured field required by its own flag, as the backend emits a required config property",
      { name: "code", label: "Code", required: true, presenceRequired: true, maxLength: 8 }, undefined, { label: "Second" },
    ],
    ["a field required only by the model's required-on-create names", { name: "code", label: "Code" }, codeRequiredOnCreate, { label: "Second", code: "" }],
  ];
  test.each(requiredCodes)("hidden, %s and left empty does not block the save, which sends what the form holds", async (_shape, code, metadata, sent) => {
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { ...code, hidden: true }]} />, metadata);
    const label = await screen.findByLabelText("Label");
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual(sent);
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test.each(requiredCodes)("guard: shown, %s and left empty blocks the save with its message under it", async (_shape, code, metadata) => {
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, code]} />, metadata);
    const label = await screen.findByLabelText("Label");
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByText("Code").closest('[data-layout="stack"]')?.textContent).toContain("This field is required.");
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // A hand-declared object field: `handle` holds a value, `secret` is required and left out.
  const retryField = (secret: Partial<FormField>): FormField => ({
    name: "retry", label: "Retry", widget: "object", objectTemplate: [
      { name: "handle", label: "Handle", required: true },
      { name: "secret", label: "Secret", required: true, ...secret },
    ],
  });
  test.each<[string, Partial<FormField>]>([
    ["hidden", { hidden: true }],
    ["read-only", { readOnly: true }],
  ])("a %s required member left out does not block the save: after an edit of a sibling no message shows and the save sends exactly the values held", async (_state, secret) => {
    renderWithProviders(<FormView resource="notes.Note" fields={[retryField(secret)]} defaultValues={{ retry: { handle: "abc" } }} />);
    const handle = await screen.findByLabelText("Handle");
    fireEvent.change(handle, { target: { value: "abcd" } });
    fireEvent.blur(handle);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ retry: { handle: "abcd" } });
    expect(screen.queryByText("This field is required.")).toBeNull();
    expect(document.querySelector('[aria-invalid="true"]')).toBeNull();
  });

  test("guard: a visible, editable required member left out still blocks the save with its message under it", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[retryField({})]} defaultValues={{ retry: { handle: "abc" } }} />);
    const handle = await screen.findByLabelText("Handle");
    fireEvent.change(handle, { target: { value: "abcd" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByText("Secret").closest('[data-layout="stack"]')?.textContent).toContain("This field is required.");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // A required field in a contributed section: one the record's implementation admits only when
  // its kind is IMAP, and one only a record holding `manage` shows.
  async function saveTitle() {
    const title = await screen.findByLabelText("Title");
    await waitFor(() => expect((title as HTMLTextAreaElement).value).toBe("First"));
    fireEvent.change(title, { target: { value: "Second" } });
    fireEvent.blur(title);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  }
  const renderImplSection = (kind: string) => {
    sdkMocks.record = { id: "note-1", title: "First", kind, secret: "" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      implMetadata({ secret: { name: "secret", kind: "scalar", scalar: "String" } }), undefined,
      formChildren({ "notes.Note#sections": {
        "imap.secret": { impl: "imap", content: <Group label="IMAP"><Field name="secret" label="Secret" required /></Group> },
      } }));
  };
  const renderPermissionSection = (permissions: string[]) => {
    sdkMocks.record = { id: "note-1", title: "First", code: "", permissions };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      undefined, undefined,
      formChildren({ "notes.Note#sections": {
        "notes.private": { permission: "manage", content: <Group label="Private"><Field name="code" label="Code" required /></Group> },
      } }));
  };

  test("a required field left empty in a section the record's implementation does not admit does not block the save, which sends what the form edited", async () => {
    renderImplSection("WHATSAPP");
    await saveTitle();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second", id: "note-1" });
    expect(screen.queryByLabelText("Secret")).toBeNull();
  });

  test("guard: the same required field left empty in a section the record's implementation admits blocks the save with its message under it", async () => {
    renderImplSection("IMAP");
    await saveTitle();
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByLabelText("Secret").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a required field left empty in a contributed section whose permission the record lacks does not block the save, which sends what the form edited", async () => {
    renderPermissionSection(["read", "write"]);
    await saveTitle();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second", id: "note-1" });
    expect(screen.queryByLabelText("Code")).toBeNull();
  });

  test("guard: the same required field left empty in a section the record's permission shows blocks the save with its message under it", async () => {
    renderPermissionSection(["read", "write", "manage"]);
    await saveTitle();
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a required field the record cannot write, left empty on an edit, does not block the save", async () => {
    sdkMocks.record = { id: "note-1", label: "First", code: "" };
    const scalar = (name: string, updatable: boolean) => ({ name, kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: true, updatable, requiredOnCreate: false });
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[labelField, { name: "code", label: "Code", required: true }]} />,
      { types: { NoteType: { resource: testDataResource("notes.Note", { fields: [scalar("label", true), scalar("code", false)] }),
        fields: { label: scalar("label", true), code: scalar("code", false) } } } });
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
  });

  test("a required field left empty in a saved-only group on a create does not block the save, since a create form omits the group and its fields", async () => {
    renderWithProviders(<FormView resource="notes.Note"><Field name="label" label="Label" />
      <Group label="Saved" savedOnly><Field name="code" label="Code" required /></Group></FormView>);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
  });

  test("a saved-only group's field that is not required is not sent on a create, which omits the group and its fields", async () => {
    renderWithProviders(<FormView resource="notes.Note"><Field name="label" label="Label" />
      <Group label="Saved" savedOnly><Field name="code" label="Code" /></Group></FormView>);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
  });

  test("a contributed section whose permission no record holds, on a create: its required field does not block and its default is not sent", async () => {
    renderWithProviders(<FormView resource="notes.Note"><Field name="label" label="Label" /></FormView>, undefined, undefined,
      formChildren({ "notes.Note#sections": {
        "notes.private": { permission: "manage", content: <Group label="Private">
          <Field name="code" label="Code" required /><Field name="note" label="Note" defaultValue="seeded" />
        </Group> },
      } }));
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
    expect(screen.queryByLabelText("Code")).toBeNull();
  });

  // A create seeded with `code`, which one group declares: saved-only, or shown on a create.
  const createSeeded = (savedOnly: boolean) => {
    renderWithProviders(<FormView resource="notes.Note" defaultValues={{ code: "seeded" }}><Field name="label" label="Label" />
      <Group label="Coded" savedOnly={savedOnly}><Field name="code" label="Code" /></Group></FormView>);
    return screen.findByLabelText("Label").then((label) => {
      fireEvent.change(label, { target: { value: "Second" } });
      fireEvent.click(screen.getByRole("button", { name: "Create" }));
    });
  };

  test("a create seed naming a field only a saved-only group declares is not sent", async () => {
    await createSeeded(true);
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
  });

  test("guard: the same create seed for a field the create shows is sent", async () => {
    await createSeeded(false);
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second", code: "seeded" });
  });

  test("guard: a required field left empty in a saved-only group on an edit blocks the save with its message under it", async () => {
    sdkMocks.record = { id: "note-1", label: "First", code: "" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="label" label="Label" />
      <Group label="Saved" savedOnly><Field name="code" label="Code" required /></Group></FormView>);
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByText("Code").closest('[data-layout="stack"]')?.textContent).toContain("This field is required.");
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // `code` declared twice: by a section whose permission the record lacks, then by one it shows.
  const renderTwiceDeclared = (undisplayed: ReactElement, displayed: ReactElement) => {
    sdkMocks.record = { id: "note-1", title: "First", code: "", permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      undefined, undefined,
      formChildren({ "notes.Note#sections": {
        "notes.private": { permission: "manage", sequence: 10, content: <Group label="Private">{undisplayed}</Group> },
        "notes.public": { sequence: 20, content: <Group label="Public">{displayed}</Group> },
      } }));
  };

  test("a field an undisplayed section and a displayed one both declare is checked through the displayed declaration", async () => {
    renderTwiceDeclared(<Field name="code" label="Code" required />, <Field name="code" label="Code" />);
    await saveTitle();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second", id: "note-1" });
  });

  test("a field an undisplayed section and a displayed one both declare is saved through the displayed declaration", async () => {
    renderTwiceDeclared(<Field name="code" label="Code" readOnly />, <Field name="code" label="Code" />);
    const title = await screen.findByLabelText("Title");
    await waitFor(() => expect((title as HTMLTextAreaElement).value).toBe("First"));
    const code = screen.getByLabelText("Code");
    fireEvent.change(code, { target: { value: "abc" } });
    fireEvent.blur(code);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ code: "abc", id: "note-1" });
  });

  // Model metadata reading and writing each named text field, as a rail row needs.
  const textFieldsMetadata = (names: readonly string[]): TestSchemaMetadata => {
    const scalars = names.map((name) => ({ name, kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false }));
    return { types: { NoteType: { resource: testDataResource("notes.Note", { fields: scalars }),
      fields: Object.fromEntries(scalars.map((field) => [field.name, field])) } } };
  };
  // A required rail row, `code`, behind the permission `manage` of its group, its child or itself.
  const renderGatedRailRow = (gate: "group" | "child" | "row", permissions: string[]) => {
    sdkMocks.record = { id: "note-1", title: "First", code: "", permissions };
    const row = { field: { name: "code", label: "Code", required: true }, ...(gate === "row" ? { permission: "manage" } : {}) };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      textFieldsMetadata(["title", "code"]), undefined,
      formChildren({ "notes.Note#rail": { "notes.properties": {
        ...(gate === "child" ? { permission: "manage" } : {}),
        content: <FormView.RailGroup id="properties" label="Properties" {...(gate === "group" ? { permission: "manage" } : {})}
          fields={[row]} />,
      } } }));
  };

  test.each(["group", "child", "row"] as const)("a required rail row left empty behind its %s's permission, which the record lacks, does not block the save", async (gate) => {
    renderGatedRailRow(gate, ["read", "write"]);
    await saveTitle();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second", id: "note-1" });
    expect(screen.queryByLabelText("Code")).toBeNull();
  });

  test.each(["group", "child", "row"] as const)("guard: the same rail row behind its %s's permission, which the record holds, blocks the save with its message under it", async (gate) => {
    renderGatedRailRow(gate, ["read", "write", "manage"]);
    await saveTitle();
    const rail = await screen.findByRole("complementary");
    expect(await within(rail).findByText("This field is required.")).toBeTruthy();
    expect(within(rail).getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("guard: on a create, a required rail row and a rail row with a default, in a group no row decides, neither block the save nor send the default", async () => {
    renderWithProviders(<FormView resource="notes.Note"><Field name="title" label="Title" title /></FormView>,
      textFieldsMetadata(["title", "code", "note"]), undefined,
      formChildren({ "notes.Note#rail": { "notes.properties": {
        content: <FormView.RailGroup id="properties" label="Properties" fields={[
          { field: { name: "code", label: "Code", required: true } },
          { field: { name: "note", label: "Note", defaultValue: "seeded" } },
        ]} />,
      } } }));
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second" });
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  test("guard: a form's sections, with their keys, order, labels and sequences, stay those it showed for a create and for each record", async () => {
    const page = <>
      <Field name="title" label="Title" title />
      <Group label="Details"><Field name="label" label="Label" /></Group>
      <Group label="Saved" savedOnly><Field name="code" label="Code" /></Group>
      <Group label="Dates" savedOnly pane="dates"><Field name="due" label="Due" /></Group>
    </>;
    const runtime = formChildren({ "notes.Note#sections": {
      "notes.private": { permission: "manage", sequence: 5, content: <Group label="Private"><Field name="secret_note" label="Secret note" /></Group> },
      "imap.settings": { impl: "imap", sequence: 20, content: <Group label="IMAP"><Field name="host" label="Host" /></Group> },
      "notes.extra": { sequence: 30, content: <Group label="Extra"><Field name="extra" label="Extra field" /></Group> },
    } });
    const labels = ["Details", "Saved", "Dates", "Private", "IMAP", "Extra"];
    // The surface's sheet sections then its pane sections, and the section headings on screen, in order.
    const shown = async (id: string | null, record?: Row) => {
      cleanup();
      if (record) sdkMocks.record = record;
      let sections: unknown[] | null = null;
      renderWithProviders(<>
        <FormView resource="notes.Note" id={id}>{page}</FormView>
        <SectionsProbe resource="notes.Note" id={id} seen={(seen) => { sections = seen; }}>{page}</SectionsProbe>
      </>, implMetadata(), undefined, runtime);
      await screen.findByText("Details");
      await waitFor(() => expect(sections).not.toBeNull());
      // The form shows the record it loaded, and the sections it decides for that record.
      if (record) await screen.findByDisplayValue(String(record.title));
      const headings = screen.getAllByRole("heading").map((node) => node.textContent ?? "").filter((text) => labels.includes(text));
      return { sections, headings };
    };

    expect(await shown(null)).toEqual({
      sections: [["group:0:Details", "Details", 0, undefined], ["group:4:Extra", "Extra", 30, undefined]],
      headings: ["Details", "Extra"],
    });
    expect(await shown("note-1", { id: "note-1", title: "First", kind: "IMAP", permissions: ["read", "write", "manage"] })).toEqual({
      sections: [
        ["group:0:Details", "Details", 0, undefined], ["group:1:Saved", "Saved", 0, undefined],
        ["group:3:Private", "Private", 5, undefined], ["group:4:IMAP", "IMAP", 20, undefined],
        ["group:5:Extra", "Extra", 30, undefined], ["group:2:Dates", "Dates", 0, "dates"],
      ],
      headings: ["Details", "Saved", "Private", "IMAP", "Extra", "Dates"],
    });
    expect(await shown("note-2", { id: "note-2", title: "First", kind: "WHATSAPP", permissions: ["read", "write"] })).toEqual({
      sections: [
        ["group:0:Details", "Details", 0, undefined], ["group:1:Saved", "Saved", 0, undefined],
        ["group:5:Extra", "Extra", 30, undefined], ["group:2:Dates", "Dates", 0, "dates"],
      ],
      headings: ["Details", "Saved", "Extra", "Dates"],
    });
  });

  test("a page's own status bar stays in the header where a contribution the record displays re-declares the field plainly", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field name="status" label="Status" widget="statusbar" options={statusOptions} />
    </FormView>, undefined, undefined, formChildren({ "notes.Note#sections": {
      "notes.workflow": { content: <Group label="Workflow"><Field name="status" label="Status" /></Group> },
    } }));
    const title = await screen.findByDisplayValue("First");
    expect(within(title.closest("header")!).getByRole("list")).toBeTruthy();
    expect(screen.queryByLabelText("Status")).toBeNull();
  });

  test("the required check of a `fields` declaration still blocks the save of an edit where a group re-declares the field plainly", async () => {
    sdkMocks.record = { id: "note-1", label: "First", code: "" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[labelField, { name: "code", label: "Code", required: true }]}
      groups={[{ label: "Coded", fields: [{ name: "code", label: "Code" }], actions: [] }]} />);
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a page's own title stays and is sent where a contribution the record does not display re-declares it", async () => {
    sdkMocks.record = { id: "note-1", title: "First", permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      undefined, undefined,
      formChildren({ "notes.Note#sections": {
        "notes.private": { permission: "manage", content: <Group label="Private"><Field name="title" label="Private title" /></Group> },
      } }));
    await saveTitle();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second", id: "note-1" });
  });

  test("an original section and the variant the record admits, declaring two long texts in turn, keep the variant's body and control order", async () => {
    sdkMocks.record = { id: "note-1", title: "First", kind: "IMAP", notes: "", memo: "" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      implMetadata({ notes: { name: "notes", kind: "scalar", scalar: "String" }, memo: { name: "memo", kind: "scalar", scalar: "String" } }),
      undefined,
      formChildren({ "notes.Note#sections": {
        "notes.base": { content: <Group label="Base">
          <Field name="notes" label="Notes" widget="textarea" /><Field name="memo" label="Memo" widget="textarea" />
        </Group> },
        "imap.base": { variant: { of: "notes.base", impl: "imap" }, content: <Group label="IMAP base">
          <Field name="memo" label="Memo" widget="textarea" /><Field name="notes" label="Notes" widget="textarea" />
        </Group> },
      } }));
    await screen.findByText("IMAP base");
    // The body is the variant's first long text; the other stays in its section.
    await waitFor(() => expect([...document.querySelectorAll("textarea")].map((node) => node.getAttribute("aria-label")))
      .toEqual(["Title", "Memo", "Notes"]));
  });

  // The page's own required `code`, outside its groups, re-declared by a section behind `manage`.
  const renderRedeclaredCode = (permissions: string[]) => {
    sdkMocks.record = { id: "note-1", title: "First", code: "", permissions };
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title /><Field name="code" label="Code" required />
    </FormView>, undefined, undefined,
    formChildren({ "notes.Note#sections": {
      "notes.private": { permission: "manage", content: <Group label="Private"><Field name="code" label="Code" required /></Group> },
    } }));
  };

  test("a page's own required field that only a section the record does not display re-declares shows among its ungrouped fields, and left empty blocks the save with its message under it", async () => {
    renderRedeclaredCode(["read", "write"]);
    await saveTitle();
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(screen.getByText("Code").closest('[data-layout="stack"]')?.textContent).toContain("This field is required.");
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByText("Private")).toBeNull();
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("guard: the same field, where the record holds the section's permission, shows in that section and not among the ungrouped fields", async () => {
    renderRedeclaredCode(["read", "write", "manage"]);
    const section = (await screen.findByText("Private")).closest("section")!;
    expect(section.contains(screen.getByLabelText("Code"))).toBe(true);
  });

  test("guard: a field of the page's own saved-only group shows nowhere on a create, never falling back to the ungrouped fields", async () => {
    renderWithProviders(<FormView resource="notes.Note"><Field name="label" label="Label" />
      <Group label="Saved" savedOnly><Field name="code" label="Code" /></Group></FormView>);
    await screen.findByLabelText("Label");
    expect(screen.queryByLabelText("Code")).toBeNull();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  test("a redacted required field left empty does not block the save", async () => {
    sdkMocks.record = { id: "note-1", label: "First", secret: null, redacted_fields: ["secret"], permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[labelField, { name: "secret", label: "Private note", required: true }]} />,
      { types: { NoteType: { ...defaultModel("NoteType", "notes.Note"), fields: {
        label: { name: "label", kind: "scalar", scalar: "String" },
        secret: { name: "secret", kind: "scalar", scalar: "String" },
        redacted_fields: { name: "redacted_fields", kind: "list", scalar: "String" },
        permissions: { name: "permissions", kind: "list", scalar: "String" },
      } } } });
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
  });

  test("a redacted required field left empty shows its lock and no message, and the save sends what the form edited", async () => {
    sdkMocks.record = { id: "note-1", label: "First", secret: null, redacted_fields: ["secret"], permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[labelField, { name: "secret", label: "Private note", required: true }]} />,
      { types: { NoteType: { ...defaultModel("NoteType", "notes.Note"), fields: {
        label: { name: "label", kind: "scalar", scalar: "String" },
        secret: { name: "secret", kind: "scalar", scalar: "String" },
        redacted_fields: { name: "redacted_fields", kind: "list", scalar: "String" },
        permissions: { name: "permissions", kind: "list", scalar: "String" },
      } } } });
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    expect(screen.getByLabelText("Restricted: Private note")).toBeTruthy();
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second", id: "note-1" });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  // A saved note whose `secret` the record redacts; changing `kind` makes its preset write `secret`.
  const redactedMetadata: TestSchemaMetadata = { types: { NoteType: { ...defaultModel("NoteType", "notes.Note"), fields: {
    label: { name: "label", kind: "scalar", scalar: "String" }, kind: { name: "kind", kind: "scalar", scalar: "String" },
    secret: { name: "secret", kind: "scalar", scalar: "String" },
    redacted_fields: { name: "redacted_fields", kind: "list", scalar: "String" },
    permissions: { name: "permissions", kind: "list", scalar: "String" },
  } } } };
  const renderRedactedPreset = (submit?: FormSubmit) => {
    sdkMocks.record = { id: "note-1", label: "First", kind: "a", secret: null, redacted_fields: ["secret"], permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" submit={submit} fields={[
      { name: "label", label: "Label" },
      { name: "kind", label: "Kind", prefill: (value: unknown) => ({ secret: `from-${String(value)}` }) },
      { name: "secret", label: "Secret" },
    ]} />, redactedMetadata);
  };
  // Changes the loaded record's `kind` (its preset writes `secret`) and saves.
  async function saveKind() {
    const kind = await screen.findByLabelText("Kind") as HTMLInputElement;
    await waitFor(() => expect(kind.value).toBe("a"));
    fireEvent.change(kind, { target: { value: "b" } });
    fireEvent.blur(kind);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  }

  test("a redacted field the form's own preset wrote is sent by the save, and its lock still shows", async () => {
    renderRedactedPreset();
    await saveKind();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ kind: "b", secret: "from-b", id: "note-1" });
    expect(screen.getByLabelText("Restricted: Secret")).toBeTruthy();
  });

  test("guard: a redacted field a preset resets once its own field is hidden is sent by the save (a hidden reset)", async () => {
    // The loaded `secret` is the record's placeholder; changing `kind` hides it and its preset writes it.
    sdkMocks.record = { id: "note-1", label: "First", kind: "a", secret: "***", redacted_fields: ["secret"], permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[
      { name: "label", label: "Label" },
      { name: "kind", label: "Kind", prefill: (value: unknown) => ({ secret: `from-${String(value)}` }) },
      { name: "secret", label: "Secret", showWhen: (values) => values.kind !== "b" },
    ]} />, redactedMetadata);
    await saveKind();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ kind: "b", secret: "from-b", id: "note-1" });
  });

  test("a redacted field the form's own preset wrote is in a custom submit owner's data", async () => {
    const submit = vi.fn<FormSubmit>(async (data) => ({ status: "ok", data: { id: "note-1", label: "First", ...data } }));
    renderRedactedPreset(submit);
    await saveKind();
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ kind: "b", secret: "from-b" });
  });

  test("guard: a redacted field the form did not write is never sent on an edit, also when a sibling is changed", async () => {
    renderRedactedPreset();
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second", id: "note-1" });
  });

  test("a server refusal of a redacted field the form wrote and sent shows in the banner by its name, called highlighted nowhere", async () => {
    sdkMocks.mutate.mockRejectedValue({ graphQLErrors: [{
      message: "Validation failed.", extensions: { code: "VALIDATION", validationErrors: { secret: ["Secret is refused."] }, formErrors: [] },
    }] });
    renderRedactedPreset();
    await saveKind();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toMatchObject({ secret: "from-b" });
    const banner = () => screen.queryAllByRole("alert").map((node) => node.textContent ?? "").join(" ");
    await waitFor(() => expect(banner()).toContain("secret: Secret is refused."));
    expect(banner()).not.toContain("highlighted");
  });

  // Notes metadata whose `label` and `extra` fields the record writes and whose `fixed` ones it does
  // not: neither updatable on an edit nor among the create fields on a create.
  const fixedFieldsMetadata = (fixed: readonly string[], extra: readonly string[] = []): TestSchemaMetadata => {
    const names = [...new Set(["label", ...fixed, ...extra])];
    const scalar = (name: string) => ({ name, kind: "scalar" as const, scalar: "String", readable: true,
      aggregatable: false, creatable: !fixed.includes(name), updatable: !fixed.includes(name), requiredOnCreate: false });
    return { types: { NoteType: { resource: testDataResource("notes.Note", {
      createFields: names.filter((name) => !fixed.includes(name)), fields: names.map(scalar),
    }), fields: Object.fromEntries(names.map((name) => [name, scalar(name)])) } } };
  };
  // The stack around a field's label: its control, and the message under it.
  const fieldStack = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')!;

  // On an edit the field is displayed read-only; on a create, holding no value, it is not displayed (a seeded one is, read-only).
  test.each([
    ["an edit", "note-1", "Save", undefined],
    ["a create, seeded", null, "Create", { code: "seeded" }],
  ] as const)("on %s, a required field the save cannot write shows a read-only control without aria-required, and does not block the save", async (_mode, id, verb, seed) => {
    sdkMocks.record = { id: "note-1", label: "First", code: "" };
    renderWithProviders(<FormView resource="notes.Note" id={id} defaultValues={seed} fields={[labelField, { name: "code", label: "Code", required: true }]} />,
      fixedFieldsMetadata(["code"]));
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    if (id) await waitFor(() => expect(label.value).toBe("First"));
    expect(screen.queryByRole("textbox", { name: "Code" })).toBeNull();
    expect(fieldStack("Code").querySelector("[aria-required]")).toBeNull();
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: verb }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual(id ? { label: "Second", id } : { label: "Second" });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test("on a create, a required field the save cannot write, holding no value, is not displayed and does not block the save", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code", required: true }]} />,
      fixedFieldsMetadata(["code"]));
    const label = await screen.findByLabelText("Label");
    expect(screen.queryByText("Code")).toBeNull();
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test.each([
    ["holding an empty object, is not displayed", undefined, false],
    ["holding an object with a member, is displayed read-only", { config: { region: "eu" } }, true],
  ] as const)("on a create, a field the save cannot write, %s", async (_case, seed, displayed) => {
    renderWithProviders(<FormView resource="notes.Note" defaultValues={seed}
      fields={[labelField, { name: "config", label: "Config", widget: "json" }]} />, fixedFieldsMetadata(["config"]));
    await screen.findByLabelText("Label");
    expect(screen.queryByText("Config") !== null).toBe(displayed);
  });

  test("guard: on an edit, a field the save cannot write holding an empty object is displayed", async () => {
    sdkMocks.record = { id: "note-1", label: "First", config: {} };
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      fields={[labelField, { name: "config", label: "Config", widget: "json" }]} />, fixedFieldsMetadata(["config"]));
    await waitFor(() => expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe("First"));
    expect(screen.getByText("Config")).toBeTruthy();
  });

  test("guard: a create sends the empty object a required field it writes holds, with no message: the check and the payload are unchanged", async () => {
    renderWithProviders(<FormView resource="notes.Note"
      fields={[labelField, { name: "config", label: "Config", widget: "json", required: true }]} />, fixedFieldsMetadata(["code"], ["config"]));
    const label = await screen.findByLabelText("Label");
    expect(screen.getByText("Config")).toBeTruthy();
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second", config: {} });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test.each([["an edit", "note-1", "Save"], ["a create", null, "Create"]] as const)("guard: on %s, a payload that touches only fields the save writes is what it was", async (_mode, id, verb) => {
    sdkMocks.record = { id: "note-1", label: "First", note: "Kept", code: "" };
    renderWithProviders(<FormView resource="notes.Note" id={id}
      fields={[labelField, { name: "note", label: "Note" }, { name: "code", label: "Code" }]} />, fixedFieldsMetadata(["code"], ["note"]));
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    if (id) await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByRole("button", { name: verb }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual(id ? { label: "Second", note: "Changed", id } : { label: "Second", note: "Changed" });
  });

  test("guard: under a custom submit owner a field the model does not let the generated save write stays editable and is submitted", async () => {
    sdkMocks.record = { id: "note-1", label: "First", code: "" };
    const submit = vi.fn(async (data: Record<string, unknown>) => ({ status: "ok" as const, data: { id: "note-1", label: "First", ...data } }));
    renderWithProviders(<FormView resource="notes.Note" id="note-1" submit={submit}
      fields={[labelField, { name: "code", label: "Code", required: true }]} />, fixedFieldsMetadata(["code"]));
    const code = await screen.findByRole("textbox", { name: "Code" }) as HTMLInputElement;
    expect(fieldStack("Code").querySelector('[aria-required="true"]')).toBeTruthy();
    fireEvent.change(code, { target: { value: "abc" } });
    fireEvent.blur(code);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![0]).toStrictEqual({ code: "abc" });
  });

  // `phase` through a registered widget that writes its own value (a button calling `ownWrite`, off
  // when read-only), or through the same control registered without saying so.
  const ownWrite = vi.fn();
  function OwnWriteControl({ value, readOnly, field }: WidgetRenderProps) {
    return <button type="button" aria-label={String(field?.label ?? "")} disabled={readOnly}
      onClick={() => ownWrite(value)}>{String(value ?? "")}</button>;
  }
  const ownWriteRuntime = { widgets: { ...defaultWidgets,
    "test.ownWrite": { writesOwnValue: true, read: OwnWriteControl, edit: OwnWriteControl },
    "test.formWrite": { read: OwnWriteControl, edit: OwnWriteControl },
  } };
  const renderPhase = ({ id = "note-1", widget = "test.ownWrite", fixed = ["phase"], required = false, phase = "", seed }: {
    id?: string | null; widget?: string; fixed?: string[]; required?: boolean; phase?: string; seed?: Record<string, unknown>;
  }) => {
    ownWrite.mockClear();
    sdkMocks.record = { id: "note-1", label: "First", phase };
    renderWithProviders(<FormView resource="notes.Note" id={id} defaultValues={seed}
      fields={[{ name: "label", label: "Label" }, { name: "phase", label: "Phase", widget, required }]} />,
      fixedFieldsMetadata(fixed, ["phase"]), undefined, ownWriteRuntime);
  };
  const phaseButton = () => screen.findByRole("button", { name: "Phase" }) as Promise<HTMLButtonElement>;
  // Edits the label on the loaded record and saves.
  async function saveLabel() {
    const label = await screen.findByLabelText("Label") as HTMLInputElement;
    await waitFor(() => expect(label.value).toBe("First"));
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  }

  test("guard: on an edit, a widget that writes its own value, on a field the save cannot write, stays enabled and its own write happens", async () => {
    renderPhase({ phase: "one" });
    const phase = await phaseButton();
    await waitFor(() => expect(phase.textContent).toBe("one"));
    expect(phase.disabled).toBe(false);
    fireEvent.click(phase);
    expect(ownWrite).toHaveBeenCalledWith("one");
  });

  test("on an edit, a required field the save cannot write, left empty, does not block the save though its widget writes its own value", async () => {
    renderPhase({ required: true });
    expect((await phaseButton()).disabled).toBe(false);
    await saveLabel();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second", id: "note-1" });
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test("on an edit, a required field the save cannot write, left empty, whose widget writes through the form, shows read-only and does not block the save", async () => {
    renderPhase({ widget: "test.formWrite", required: true });
    await saveLabel();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect((await phaseButton()).disabled).toBe(true);
    expect(screen.queryByText("This field is required.")).toBeNull();
  });

  test("guard: a required field the save can write, left empty, still blocks the save with its message though its widget writes its own value", async () => {
    renderPhase({ fixed: [], required: true });
    await saveLabel();
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    expect(fieldStack("Phase").textContent).toContain("This field is required.");
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("guard: on a create the save can write the field of, a widget that writes its own value stays enabled", async () => {
    renderPhase({ id: null, fixed: [] });
    const phase = await phaseButton();
    await waitFor(() => expect(phase.disabled).toBe(false));
    fireEvent.click(phase);
    expect(ownWrite).toHaveBeenCalledTimes(1);
  });

  test("on a create, a widget that writes its own value, on a field the create cannot write, is read-only: the flag changes nothing on a create", async () => {
    renderPhase({ id: null, seed: { phase: "one" } });
    expect((await phaseButton()).disabled).toBe(true);
  });

  test("guard: a form whose only changeable control is a widget that writes its own value, every other field one the save cannot write, does not lock and the widget writes", async () => {
    renderPhase({ fixed: ["label", "phase"], phase: "one" });
    const phase = await phaseButton();
    await waitFor(() => expect(phase.textContent).toBe("one"));
    expect(phase.disabled).toBe(false);
    fireEvent.click(phase);
    expect(ownWrite).toHaveBeenCalledWith("one");
  });

  test("a control the page renders for a path the record redacts is read-only, though its widget writes its own value", async () => {
    sdkMocks.record = { id: "note-1", label: "First", secret: null, redacted_fields: ["secret"], permissions: ["read", "write"] };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "label", label: "Label" }, { name: "secret", label: "Secret", hidden: true }]}
      formExtras={({ form }) => <BoundDescriptorField form={form} resource="notes.Note" field={{ name: "secret", label: "Bound secret", widget: "test.ownWrite" }} />} />,
      redactedMetadata, undefined, ownWriteRuntime);
    await waitFor(() => expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe("First"));
    expect((await screen.findByRole("button", { name: "Bound secret" }) as HTMLButtonElement).disabled).toBe(true);
  });

  // A control the page renders itself (`formExtras`) for `code`, which only a group hidden from the
  // sheet declares: a saved-only one on a create, or one the create shows.
  const renderPageControl = (savedOnly: boolean) => {
    renderWithProviders(<FormView resource="notes.Note"
      formExtras={({ form }) => <BoundDescriptorField form={form} resource="notes.Note" field={{ name: "code", label: "Code" }} />}>
      <Field name="label" label="Label" />
      <Group label="Coded" savedOnly={savedOnly}><Field name="code" label="Code" hidden /></Group>
    </FormView>);
  };

  test("on a create, a control the page renders for a field only a saved-only group declares is read-only, and nothing is sent for it", async () => {
    renderPageControl(true);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    expect(screen.getByText("Code")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Code" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second" });
  });

  test("guard: the same control for a field of a group the create displays is editable and sent", async () => {
    renderPageControl(false);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Code" }), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ label: "Second", code: "abc" });
  });

  // A saved note whose page renders its own control for `memo`, a path no form field declares, saved by
  // the form's own submit owner or by the generated save.
  const renderUndeclaredControl = (submit?: FormSubmit) => {
    sdkMocks.record = { id: "note-1", label: "First" };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" submit={submit} fields={[labelField]}
      formExtras={({ form }) => <BoundDescriptorField form={form} resource="notes.Note" field={{ name: "memo", label: "Memo" }} />} />);
  };

  test("under a custom submit owner, a control the page renders for a path no form field declares stays editable, and the owner receives what is typed there", async () => {
    const submit = vi.fn<FormSubmit>(async (data) => ({ status: "ok", data: { id: "note-1", label: "First", ...data } }));
    renderUndeclaredControl(submit);
    await waitFor(() => expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe("First"));
    fireEvent.change(await screen.findByRole("textbox", { name: "Memo" }), { target: { value: "typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0]![1].values).toMatchObject({ memo: "typed" });
  });

  test("guard: without the custom submit owner, the same control is read-only", async () => {
    renderUndeclaredControl();
    await waitFor(() => expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe("First"));
    expect(screen.getByText("Memo")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Memo" })).toBeNull();
  });

  // A required rail row `code`, whose field the metadata reads, lacks, or marks unreadable.
  const renderReadRailRow = (read: "reads" | "lacks" | "marks unreadable") => {
    sdkMocks.record = { id: "note-1", title: "First", code: "" };
    const metadata = textFieldsMetadata(read === "lacks" ? ["title"] : ["title", "code"]);
    const note = metadata.types.NoteType!;
    renderWithProviders(<FormView resource="notes.Note" id="note-1"><Field name="title" label="Title" title /></FormView>,
      read === "marks unreadable"
        ? { types: { NoteType: { ...note, fields: { ...note.fields, code: { ...note.fields.code!, readable: false } } } } }
        : metadata, undefined,
      formChildren({ "notes.Note#rail": { "notes.properties": {
        content: <FormView.RailGroup id="properties" label="Properties" fields={[{ field: { name: "code", label: "Code", required: true } }]} />,
      } } }));
  };

  test.each(["lacks", "marks unreadable"] as const)("a required rail row whose field the metadata %s, left empty, is neither displayed nor checked", async (read) => {
    renderReadRailRow(read);
    await saveTitle();
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ title: "Second", id: "note-1" });
    expect(screen.queryByLabelText("Code")).toBeNull();
  });

  test("guard: the same rail row whose field the metadata reads shows, and left empty blocks the save with its message under it", async () => {
    renderReadRailRow("reads");
    await saveTitle();
    const rail = await screen.findByRole("complementary");
    expect(await within(rail).findByText("This field is required.")).toBeTruthy();
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // Notes metadata whose create writes only `creatable`, and whose edit writes only `label` and `summary`;
  // `owner` is a relation to users.
  const createWritesMetadata = (creatable: readonly string[] = ["label", "summary"]): TestSchemaMetadata => {
    const base = { readable: true, aggregatable: false, requiredOnCreate: false };
    const fields = {
      ...Object.fromEntries(["label", "summary", "note", "status", "description", "title"].map((name) => [name, {
        ...base, name, kind: "scalar" as const, scalar: "String",
        creatable: creatable.includes(name), updatable: name === "label" || name === "summary",
      }])),
      owner: { ...base, name: "owner", kind: "relation" as const, relationModelLabel: "iam.User", relationObject: true,
        creatable: creatable.includes("owner"), updatable: false },
    };
    return { types: { NoteType: { resource: testDataResource("notes.Note", {
      createFields: [...creatable], fields: Object.values(fields),
    }), fields } } };
  };
  const owner = { name: "owner", label: "Owner" } satisfies FormField;
  const note = { name: "note", label: "Note", description: "Shown to readers" } satisfies FormField;
  const draftStatus = { name: "status", label: "Status", widget: "statusbar", status: true, options: statusOptions } satisfies FormField;

  test("on a create, a relation and a text the save cannot write, holding no value, display nothing: no label, no row", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "label", label: "Label" }, owner, note]} />, createWritesMetadata());
    await screen.findByRole("textbox", { name: "Label" });
    expect(screen.queryByText("Owner")).toBeNull();
    expect(screen.queryByText("Note")).toBeNull();
    expect(screen.queryByText("Shown to readers")).toBeNull();
  });

  test.each<[string, Partial<FormField>, Record<string, unknown> | undefined]>([
    ["its default", { defaultValue: "kept" }, undefined],
    ["a create seed", {}, { note: "kept", owner: "user-1" }],
  ])("guard: on a create, the same fields holding a value (%s) are displayed read-only with it", async (_source, own, seed) => {
    renderWithProviders(<FormView resource="notes.Note" defaultValues={seed} fields={[{ name: "label", label: "Label" },
      { ...owner, ...(own.defaultValue ? { defaultValue: "user-1" } : {}) }, { ...note, ...own }]} />, createWritesMetadata());
    await screen.findByRole("textbox", { name: "Label" });
    expect(fieldStack("Note").textContent).toContain("kept");
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    expect(screen.getByText("Owner")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Owner" })).toBeNull();
  });

  test.each<[string, Row]>([
    ["holding no value", { id: "note-1", label: "First", note: "", owner: null }],
    ["holding a value", { id: "note-1", label: "First", note: "kept", owner: { id: "user-1" } }],
  ])("guard: on an edit, the same fields, %s, are displayed read-only", async (_case, record) => {
    sdkMocks.record = record;
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "label", label: "Label" }, owner, note]} />,
      createWritesMetadata());
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Label" }) as HTMLInputElement).value).toBe("First"));
    expect(screen.getByText("Owner")).toBeTruthy();
    expect(fieldStack("Note").textContent).toContain(`${record.note}`);
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Owner" })).toBeNull();
  });

  test.each<[string, Partial<FormField>, boolean]>([
    ["an empty status the save cannot write is not displayed, by the general rule", {}, false],
    ["a status the save cannot write, holding its default, is displayed read-only", { defaultValue: "DRAFT" }, true],
  ])("guard: on a create, %s", async (_case, own, displayed) => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "title", label: "Title", title: true }, { ...draftStatus, ...own }]} />,
      createWritesMetadata(["title"]));
    const header = (await screen.findByRole("textbox", { name: "Title" })).closest("header")!;
    expect(within(header).queryAllByRole("list").length > 0).toBe(displayed);
    if (displayed) expect(within(header).getByText("Draft")).toBeTruthy();
  });

  test("on a create, a status the save cannot write, holding only blanks, holds nothing to display and is not displayed", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "title", label: "Title", title: true }, { ...draftStatus, defaultValue: "  " }]} />,
      createWritesMetadata(["title"]));
    const header = (await screen.findByRole("textbox", { name: "Title" })).closest("header")!;
    expect(within(header).queryAllByRole("list")).toEqual([]);
  });

  test("guard: on a create, an empty status declared read-only is not displayed even where the save could write it", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "title", label: "Title", title: true }, { ...draftStatus, readOnly: true }]} />,
      createWritesMetadata(["title", "status"]));
    const header = (await screen.findByRole("textbox", { name: "Title" })).closest("header")!;
    expect(within(header).queryAllByRole("list")).toEqual([]);
  });

  test.each<[string, Partial<FormField>, boolean]>([
    ["an empty status the create input takes, on a create that takes no edit, is not displayed", {}, false],
    ["the same status holding its default is displayed", { defaultValue: "DRAFT" }, true],
  ])("guard: %s", async (_case, own, displayed) => {
    renderWithProviders(<FormView resource="notes.Note" readOnly fields={[{ name: "title", label: "Title", title: true }, { ...draftStatus, ...own }]} />,
      createWritesMetadata(["title", "status"]));
    const header = (await screen.findByRole("heading", { level: 1 })).closest("header")!;
    expect(within(header).queryAllByRole("list").length > 0).toBe(displayed);
  });

  test("guard: on a create, a field the save writes, holding no value, is displayed and takes edits", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "label", label: "Label" }, { name: "summary", label: "Summary" }]} />,
      createWritesMetadata());
    expect(await screen.findByRole("textbox", { name: "Summary" })).toBeTruthy();
  });

  test("on a create, a body the save cannot write, holding no value, is not displayed", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "label", label: "Label" },
      { name: "description", label: "Description", widget: "textarea" }]} />, createWritesMetadata());
    await screen.findByRole("textbox", { name: "Label" });
    expect(screen.queryByText("Description")).toBeNull();
  });

  test("guard: on a create, a title the save cannot write, holding no value, leaves the create's own heading", async () => {
    renderWithProviders(<FormView resource="notes.Note" fields={[{ name: "title", label: "Title", title: true },
      { name: "label", label: "Label" }]} />, createWritesMetadata());
    await screen.findByRole("textbox", { name: "Label" });
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("New note");
  });

  test("on a create, a section left with no field the create displays keeps its key and place, and renders no heading", async () => {
    const page = <>
      <Group label="Details"><Field name="label" label="Label" /><Field name="owner" label="Owner" /></Group>
      <Group label="Notes"><Field name="note" label="Note" /></Group>
      <Group label="More"><Field name="summary" label="Summary" /></Group>
    </>;
    let sections: unknown[] | null = null;
    renderWithProviders(<>
      <FormView resource="notes.Note">{page}</FormView>
      <SectionsProbe resource="notes.Note" id={null} seen={(seen) => { sections = seen; }}>{page}</SectionsProbe>
    </>, createWritesMetadata());
    await screen.findByRole("textbox", { name: "Summary" });
    await waitFor(() => expect(sections).toEqual([
      ["group:0:Details", "Details", 0, undefined], ["group:1:Notes", "Notes", 0, undefined], ["group:2:More", "More", 0, undefined],
    ]));
    expect(screen.getAllByRole("heading").map((node) => node.textContent).filter((text) => ["Details", "Notes", "More"].includes(text ?? "")))
      .toEqual(["Details", "More"]);
  });

  // A create whose `label` presets `code`, which the create cannot write, in a section or in a pane.
  const labelPresetsCode = { name: "label", label: "Label", prefill: (value: unknown) => ({ code: value ? `C-${String(value)}` : null }) };
  // Types `value` into the label and returns what then stands: the label's node, focus and value, and `code`'s text.
  async function typeLabel(label: HTMLTextAreaElement | HTMLInputElement, value: string) {
    fireEvent.change(label, { target: { value } });
    await act(() => Promise.resolve());
    const now = screen.getByRole("textbox", { name: "Label" });
    return { sameNode: now === label, focused: document.activeElement === now, value: (now as HTMLInputElement).value };
  }

  test("guard: on a create, a section's field the save cannot write is displayed once a preset fills it and not once it is cleared, the field being typed keeping its node, focus and value", async () => {
    renderWithProviders(<FormView resource="notes.Note">
      <Field {...labelPresetsCode} />
      <Group label="Details"><Field name="code" label="Code" /></Group>
    </FormView>, createWritesMetadata(["label"]));
    const label = await screen.findByRole("textbox", { name: "Label" }) as HTMLInputElement;
    expect(screen.queryByText("Code")).toBeNull();
    label.focus();
    expect(await typeLabel(label, "ab")).toEqual({ sameNode: true, focused: true, value: "ab" });
    await waitFor(() => expect(fieldStack("Code").textContent).toContain("C-ab"));
    expect(await typeLabel(label, "")).toEqual({ sameNode: true, focused: true, value: "" });
    await waitFor(() => expect(screen.queryByText("Code")).toBeNull());
  });

  // Two panes on a create: `Extra` holds only `code`, which the create cannot write; `More` holds `note`.
  const renderCreatePanes = (extra: ReactNode = <Field name="code" label="Code" />, id: string | null = null) => {
    renderWithProviders(<FormView resource="notes.Note" id={id}>
      <Field {...labelPresetsCode} />
      <Group label="Extra" pane="extra">{extra}</Group>
      <Group label="More" pane="more"><Field name="note" label="Note" /></Group>
    </FormView>, createWritesMetadata(["label", "note"]));
  };

  test("on a create, a pane holding only fields the save cannot write and that hold no value is not listed, and the strip follows", async () => {
    renderCreatePanes();
    await screen.findByRole("textbox", { name: "Label" });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Note" })).toBeTruthy());
    expect(screen.queryAllByRole("tab")).toEqual([]);
    expect(screen.queryByText("Extra")).toBeNull();
    expect(screen.getByRole("heading", { name: "More" })).toBeTruthy();
  });

  test("on a create, the same pane is listed once a preset fills its field and not once it is cleared, the field being typed keeping its node, focus and value", async () => {
    renderCreatePanes();
    const label = await screen.findByRole("textbox", { name: "Label" }) as HTMLInputElement;
    label.focus();
    expect(await typeLabel(label, "ab")).toEqual({ sameNode: true, focused: true, value: "ab" });
    await waitFor(() => expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Extra", "More"]));
    expect(await typeLabel(label, "")).toEqual({ sameNode: true, focused: true, value: "" });
    await waitFor(() => expect(screen.queryAllByRole("tab")).toEqual([]));
    expect(screen.queryByText("Extra")).toBeNull();
  });

  test("guard: on a create, a pane with content of its own and no field to display is listed", async () => {
    renderWithProviders(<FormView resource="notes.Note">
      <Field name="label" label="Label" />
      <Group label="Extra" pane="extra" content={<p>Extra guidance</p>}><Field name="code" label="Code" /></Group>
      <Group label="More" pane="more"><Field name="note" label="Note" /></Group>
    </FormView>, createWritesMetadata(["label", "note"]));
    await screen.findByRole("textbox", { name: "Label" });
    await waitFor(() => expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Extra", "More"]));
  });

  test("guard: on an edit, a pane holding only a field the save cannot write, holding no value, is listed", async () => {
    sdkMocks.record = { id: "note-1", label: "First", code: "", note: "" };
    renderCreatePanes(undefined, "note-1");
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Label" }) as HTMLInputElement).value).toBe("First"));
    await waitFor(() => expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Extra", "More"]));
  });

  test("typing in a pane keeps the field's node and focus, and its pane selected, while another pane returns or is dropped", async () => {
    // A create whose `More` pane holds `note`, which presets `code`; `Extra` holds only `code`, which the
    // create cannot write, so it is listed only while `code` holds a value.
    renderWithProviders(<FormView resource="notes.Note">
      <Field name="label" label="Label" />
      <Group label="More" pane="more">
        <Field name="note" label="Note" prefill={(value: unknown) => ({ code: value ? `N-${String(value)}` : null })} />
      </Group>
      <Group label="Extra" pane="extra"><Field name="code" label="Code" /></Group>
    </FormView>, createWritesMetadata(["label", "note"]));
    const note = await screen.findByRole("textbox", { name: "Note" });
    const typing = () => {
      const now = screen.getByRole("textbox", { name: "Note" });
      return { sameNode: now === note, focused: document.activeElement === now };
    };
    note.focus();
    fireEvent.change(note, { target: { value: "q" } });
    await waitFor(() => expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["More", "Extra"]));
    expect(screen.getByRole("tab", { name: "More" }).getAttribute("aria-selected")).toBe("true");
    expect(typing()).toEqual({ sameNode: true, focused: true });
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "" } });
    await waitFor(() => expect(screen.queryAllByRole("tab")).toEqual([]));
    expect(typing()).toEqual({ sameNode: true, focused: true });
  });

  // A saved note whose lone `Draft` tab holds an input of its own; `Activity` is listed while the record's
  // words are not 3, the count the tests start from.
  const draftThenActivity = [
    { id: "draft", label: "Draft", render: () => <input aria-label="Draft" /> },
    { id: "activity", label: "Activity", visibleWhen: (record: Row) => String(record.wordCount) !== "3",
      render: () => <p>Activity panel</p> },
  ];
  // What stands where the person typed in `Draft`: the same node, focused, holding what they typed.
  const draftTyping = (draft: HTMLElement) => {
    const now = screen.getByRole("textbox", { name: "Draft" });
    return { sameNode: now === draft, focused: document.activeElement === now, value: (now as HTMLInputElement).value };
  };
  const tabNames = () => screen.queryAllByRole("tab").map((tab) => tab.textContent);

  test("typing in a lone pane keeps the field's node and focus while a tab the saved record shows comes and goes", async () => {
    sdkMocks.mutate.mockImplementation(async ({ data }: { data: Row }) => (sdkMocks.record = { ...sdkMocks.record, ...data }));
    renderWithProviders(<FormView resource="notes.Note" id="note-1" recordTabs={draftThenActivity}
      fields={[{ name: "title", label: "Title", title: true }, { name: "wordCount", label: "Words" }]} />);
    const draft = await screen.findByRole("textbox", { name: "Draft" });
    expect(tabNames()).toEqual([]);
    draft.focus();
    fireEvent.change(draft, { target: { value: "kept" } });
    const save = async (words: string, tabs: string[]) => {
      fireEvent.change(screen.getByLabelText("Words"), { target: { value: words } });
      fireEvent.click(await screen.findByRole("button", { name: "Save" }));
      await waitFor(() => expect(tabNames()).toEqual(tabs));
    };
    await save("5", ["Draft", "Activity"]);
    expect(draftTyping(draft)).toEqual({ sameNode: true, focused: true, value: "kept" });
    await save("3", []);
    expect(draftTyping(draft)).toEqual({ sameNode: true, focused: true, value: "kept" });
  });

  test("typing in a lone pane keeps the field's node and focus while the record, read again, shows a tab and hides it", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} recordTabs={draftThenActivity}
      formExtras={({ reload }) => <button type="button" onClick={() => void reload()}>Read again</button>} />);
    const draft = await screen.findByRole("textbox", { name: "Draft" });
    draft.focus();
    fireEvent.change(draft, { target: { value: "kept" } });
    const readAgain = async (wordCount: number, tabs: string[]) => {
      sdkMocks.record = { ...sdkMocks.record, wordCount };
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      await waitFor(() => expect(tabNames()).toEqual(tabs));
    };
    await readAgain(5, ["Draft", "Activity"]);
    expect(draftTyping(draft)).toEqual({ sameNode: true, focused: true, value: "kept" });
    await readAgain(3, []);
    expect(draftTyping(draft)).toEqual({ sameNode: true, focused: true, value: "kept" });
  });

  // An edit whose `Extra` pane is listed while the words are above 3, a value the sheet's `Words` field changes.
  const wordsField = { name: "wordCount", label: "Words" };
  const extraWhileWordy = <Group label="Extra" pane="extra">
    <Field name="note" label="Note" showWhen={(values) => Number(values.wordCount) > 3} />
  </Group>;
  const setWords = async (words: string, tabs: string[]) => {
    fireEvent.change(screen.getByLabelText("Words"), { target: { value: words } });
    await waitFor(() => expect(tabNames()).toEqual(tabs));
  };

  // A saved note with one line, whose save the form's own submit owner refuses on that line's label.
  const renderLinedNoteRefused = () => {
    const scalar = (name: string, type: string) => ({ name, kind: "scalar" as const, scalar: type, readable: true,
      aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false });
    const noteFields = { title: scalar("title", "String"), wordCount: scalar("wordCount", "Int"), note: scalar("note", "String") };
    sdkMocks.record = { ...sdkMocks.record, lines: [{ id: "line-1", label: "Alpha", position: 0 }] };
    const submit = vi.fn<FormSubmit>(async () => ({ status: "invalid", issues: { fieldErrors: { "lines.0.label": ["Label is refused."] }, formErrors: [] } }));
    renderWithProviders(<FormView resource="notes.Note" id="note-1" submit={submit}>
      <Field name="title" label="Title" title />
      <Field {...wordsField} />
      {extraWhileWordy}
    </FormView>, { types: { NoteType: { resource: testDataResource("notes.Note", {
      fields: Object.values(noteFields),
      linesResource: { field: "lines", modelLabel: "notes.Line", positionField: "position",
        fields: [scalar("label", "String"), scalar("position", "Int")] },
    }), fields: noteFields } } });
    return submit;
  };
  // Edits the line and saves it, which the owner refuses on the line.
  async function saveRefusedLine(submit: ReturnType<typeof renderLinedNoteRefused>) {
    fireEvent.change(await screen.findByDisplayValue("Alpha"), { target: { value: "Edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Label is refused.")).toBeTruthy();
  }

  test("guard: a lone lines pane keeps its draft rows and their errors while a second pane comes and goes", async () => {
    await saveRefusedLine(renderLinedNoteRefused());
    const draftAndError = () => ({ draft: screen.queryByDisplayValue("Edited") !== null, error: screen.queryByText("Label is refused.") !== null });
    await setWords("5", ["Lines", "Extra"]);
    expect(draftAndError()).toEqual({ draft: true, error: true });
    await setWords("3", []);
    expect(draftAndError()).toEqual({ draft: true, error: true });
  });

  test("guard: after a save that failed on its lines, a chosen pane dropped and returned gives way to the lines pane, which the failure selects again", async () => {
    sdkMocks.record = { ...sdkMocks.record, wordCount: 5 };
    const submit = renderLinedNoteRefused();
    const selected = () => screen.queryAllByRole("tab").map((tab) => `${tab.textContent}${tab.getAttribute("aria-selected") === "true" ? "*" : ""}`);
    await saveRefusedLine(submit);
    expect(selected()).toEqual(["Lines*", "Extra"]);
    fireEvent.click(screen.getByRole("tab", { name: "Extra" }));
    await waitFor(() => expect(selected()).toEqual(["Lines", "Extra*"]));
    await setWords("3", []);
    expect(screen.getByRole("heading", { name: "Lines" })).toBeTruthy();
    await setWords("7", ["Lines", "Extra"]);
    await waitFor(() => expect(selected()).toEqual(["Lines*", "Extra"]));
  });

  test("a lone tab kept mounted, put behind a tab that appears ahead of it, stays mounted hidden and keeps its own state when it is lone again", async () => {
    function Counter(): ReactElement {
      const [clicks, setClicks] = useState(0);
      return <button type="button" onClick={() => setClicks((count) => count + 1)}>Clicked {clicks}</button>;
    }
    sdkMocks.mutate.mockImplementation(async ({ data }: { data: Row }) => (sdkMocks.record = { ...sdkMocks.record, ...data }));
    // `Activity`, listed while the record's words are not 3, comes ahead of `Editor` and is selected when it
    // appears, since no pane was chosen: `Editor` is then an inactive pane, which `keepMounted` keeps mounted.
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={[{ name: "title", label: "Title", title: true }, wordsField]}
      recordTabs={[draftThenActivity[1]!, { id: "editor", label: "Editor", keepMounted: true, render: () => <Counter /> }]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Clicked 0" }));
    fireEvent.click(screen.getByRole("button", { name: "Clicked 1" }));
    const save = async (words: string, tabs: string[]) => {
      fireEvent.change(screen.getByLabelText("Words"), { target: { value: words } });
      fireEvent.click(await screen.findByRole("button", { name: "Save" }));
      await waitFor(() => expect(tabNames()).toEqual(tabs));
    };
    await save("5", ["Activity", "Editor"]);
    expect(screen.getByRole("tab", { name: "Activity" }).getAttribute("aria-selected")).toBe("true");
    const hidden = screen.getByText(/^Clicked /);
    expect(hidden.textContent).toBe("Clicked 2");
    expect(hidden.closest("[role='tabpanel']")?.hasAttribute("hidden")).toBe(true);
    await save("3", []);
    expect(screen.getByRole("button", { name: /^Clicked / }).textContent).toBe("Clicked 2");
  });

  test("guard: a lone pane is a plain region beneath its heading, and several panes are tab panels labelled by their tabs", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field {...wordsField} />
      <Group label="More" pane="more"><Field name="location" label="Location" /></Group>
      {extraWhileWordy}
    </FormView>);
    // The pane's region: the child of the form's one Tabs root that holds the pane's field.
    let region: Element = await screen.findByLabelText("Location");
    while (region.parentElement && !region.parentElement.matches("[data-orientation][data-variant]")) region = region.parentElement;
    expect(screen.getByRole("heading", { name: "More" })).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("tabpanel")).toBeNull();
    expect(Object.fromEntries([...region.attributes].map((attribute) => [attribute.name, attribute.value])))
      .toEqual({ class: "mx-auto w-full max-w-[1100px] px-6 sm:px-8 pt-3 pb-12" });
    await setWords("5", ["More", "Extra"]);
    expect(screen.getByRole("tabpanel", { name: "More" }).getAttribute("tabindex")).toBe("0");
    fireEvent.click(screen.getByRole("tab", { name: "Extra" }));
    expect(await screen.findByRole("tabpanel", { name: "Extra" })).toBeTruthy();
  });

  test("guard: a chosen pane dropped and returned is selected again, the first pane standing in meanwhile", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field {...wordsField} />
      <Group label="More" pane="more"><Field name="location" label="Location" /></Group>
      {extraWhileWordy}
    </FormView>);
    await screen.findByLabelText("Location");
    const selected = () => screen.queryAllByRole("tab").map((tab) => `${tab.textContent}${tab.getAttribute("aria-selected") === "true" ? "*" : ""}`);
    await setWords("5", ["More", "Extra"]);
    expect(selected()).toEqual(["More*", "Extra"]);
    fireEvent.click(screen.getByRole("tab", { name: "Extra" }));
    await waitFor(() => expect(selected()).toEqual(["More", "Extra*"]));
    await setWords("3", []);
    expect(screen.getByRole("heading", { name: "More" })).toBeTruthy();
    expect(screen.getByLabelText("Location")).toBeTruthy();
    await setWords("7", ["More", "Extra"]);
    expect(selected()).toEqual(["More", "Extra*"]);
    expect(screen.getByLabelText("Note")).toBeTruthy();
  });

  test("guard: an inactive pane not kept mounted has no content mounted, and one kept mounted is hidden", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} recordTabs={[
      { id: "preview", label: "Preview", render: () => <p>Preview panel</p> },
      { id: "activity", label: "Activity", render: () => <p>Activity panel</p> },
      { id: "editor", label: "Editor", keepMounted: true, render: () => <p>Editor panel</p> },
    ]} />);
    expect(await screen.findByText("Preview panel")).toBeTruthy();
    expect(screen.queryByText("Activity panel")).toBeNull();
    expect(screen.getByText("Editor panel").closest("[role='tabpanel']")?.hasAttribute("hidden")).toBe(true);
  });

  // A server refusal of `code` alone, or with a form-level error.
  const refuse = (formErrors: string[]) => sdkMocks.mutate.mockRejectedValue({ graphQLErrors: [{
    message: "Validation failed.",
    extensions: { code: "VALIDATION", validationErrors: { code: ["Code is taken."] }, formErrors },
  }] });
  const banner = () => screen.queryAllByRole("alert").map((node) => node.textContent ?? "").join(" ");

  test.each([
    ["alone", [], ["code: Code is taken."]],
    ["with a form-level error", ["Note is misconfigured."], ["Note is misconfigured.", "code: Code is taken."]],
  ])("a hidden field the server refuses %s shows the server's own text in the banner, each message its own, and no field is called highlighted", async (_case, formErrors, messages) => {
    refuse(formErrors);
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code", hidden: true }]} />);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await screen.findByText(messages.at(-1)!);
    // One message is the banner's body; several are a list, one item each, never one run-on sentence.
    const alert = screen.getByRole("alert");
    expect(within(alert).queryAllByRole("listitem").map((item) => item.textContent)).toEqual(messages.length > 1 ? messages : []);
    for (const message of messages) expect(within(alert).getByText(message)).toBeTruthy();
    expect(banner()).not.toContain("highlighted");
  });

  test("a save the server refuses for shown fields moves focus to the first of them in the form's order, not the answer's", async () => {
    sdkMocks.mutate.mockRejectedValue({ graphQLErrors: [{
      message: "Validation failed.",
      extensions: { code: "VALIDATION", validationErrors: { code: ["Code is taken."], label: ["Label is taken."] }, formErrors: [] },
    }] });
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code" }]} />);
    const label = await screen.findByLabelText("Label");
    fireEvent.change(label, { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Label is taken.");
    await screen.findByText("Code is taken.");
    expect(document.activeElement).toBe(label);
  });

  test("a save the server refuses for a shown field and for a form-level reason moves focus to its banner, which also names the field", async () => {
    sdkMocks.mutate.mockRejectedValue({ graphQLErrors: [{
      message: "Validation failed.",
      extensions: { code: "VALIDATION", validationErrors: { code: ["Code is taken."] }, formErrors: ["Note is misconfigured."] },
    }] });
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code" }]} />);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    // The person is in the field the server will refuse: the banner takes focus all the same.
    const code = screen.getByLabelText("Code");
    code.focus();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const alert = (await screen.findByText("Note is misconfigured.")).closest<HTMLElement>('[role="alert"]')!;
    await waitFor(() => expect(document.activeElement).toBe(alert));
    expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Note is misconfigured.", "Please fix the highlighted fields: Code.",
    ]);
    // The field keeps its own reason and its mark.
    expect(screen.getByText("Code is taken.")).toBeTruthy();
    expect(code.getAttribute("aria-invalid")).toBe("true");
  });

  test("a save the server refuses for no shown field moves focus to its banner", async () => {
    refuse(["Note is misconfigured."]);
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code", hidden: true }]} />);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const alert = (await screen.findByText("Note is misconfigured.")).closest<HTMLElement>('[role="alert"]')!;
    await waitFor(() => expect(document.activeElement).toBe(alert));
    expect(alert.getAttribute("tabindex")).toBe("-1");
  });

  test("a field of a group the record does not display that the server refuses shows the server's own text in the banner, called highlighted nowhere and marked invalid nowhere", async () => {
    refuse([]);
    renderWithProviders(<FormView resource="notes.Note"><Field name="label" label="Label" />
      <Group label="Saved" savedOnly><Field name="code" label="Code" /></Group></FormView>);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(banner()).toContain("code: Code is taken."));
    expect(banner()).not.toContain("highlighted");
    expect(document.querySelector('[aria-invalid="true"]')).toBeNull();
  });

  test("guard: a shown field the server refuses is highlighted, its message under it and the summary naming it", async () => {
    refuse([]);
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code" }]} />);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Please fix the highlighted fields: Code.")).toBeTruthy();
    expect(screen.getByText("Code").closest('[data-layout="stack"]')?.textContent).toContain("Code is taken.");
    expect(screen.getByLabelText("Code").getAttribute("aria-invalid")).toBe("true");
  });

  test("guard: a field declared hidden whose control the page renders itself shows the server's refusal under that control", async () => {
    refuse([]);
    renderWithProviders(<FormView resource="notes.Note" fields={[labelField, { name: "code", label: "Code", hidden: true }]}
      formExtras={({ form }) => <BoundDescriptorField form={form} resource="notes.Note" field={{ name: "code", label: "Code" }} />} />);
    fireEvent.change(await screen.findByLabelText("Label"), { target: { value: "Second" } });
    await screen.findByLabelText("Code");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("Code").closest('[data-layout="stack"]')?.textContent).toContain("Code is taken."));
  });

  test.each([
    {
      shape: "the only member of a JSON root", label: "Timeout", loaded: "30",
      record: { config: { timeout: 30 } }, fields: () => configFields({ timeout: timeoutMember }),
    },
    {
      shape: "a member of a JSON root beside a sibling", label: "Timeout", loaded: "30",
      record: { config: { timeout: 30, label: "a" } },
      fields: () => configFields({ timeout: timeoutMember, label: { type: "string", label: "Label" } }),
    },
    {
      shape: "nested in an object member of a JSON root", label: "Delay", loaded: "5",
      record: { config: { retry: { delay: 5 }, label: "a" } },
      fields: () => configFields({
        retry: { type: "object", widget: "object", label: "Retry", properties: {
          delay: { type: "integer", label: "Delay", omittable: true },
        } },
        label: { type: "string", label: "Label", omittable: true },
      }),
    },
    {
      shape: "a top-level optional field", label: "Count", loaded: "3",
      record: { count: 3 },
      fields: () => deserializeFormSpec({ type: "object", properties: {
        count: { type: "integer", label: "Count", omittable: true },
      } }, defaultWidgets),
    },
  ])("a cleared number that is $shape is not saved: the save stops at the number message", async ({ label, loaded, record, fields }) => {
    sdkMocks.record = { id: "note-1", ...record };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields()} />);
    const input = await screen.findByLabelText(label);
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(loaded));
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Enter a valid whole number.")).toBeTruthy();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
    expect(dataProvider.update).not.toHaveBeenCalled();
  });

  test("a number typed into a JSON root's member is sent as a number in its root", async () => {
    sdkMocks.record = { id: "note-1", config: { timeout: 30 } };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={configFields({ timeout: timeoutMember })} />);
    const timeout = await screen.findByLabelText("Timeout");
    await waitFor(() => expect((timeout as HTMLInputElement).value).toBe("30"));
    fireEvent.change(timeout, { target: { value: "7" } });
    fireEvent.blur(timeout);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ id: "note-1", config: { timeout: 7 } });
  });

  const handleMember = { handle: { type: "string", label: "Handle", minLength: 3 } } as const;
  const retryMember = { retry: { type: "object", widget: "object", label: "Retry", properties: handleMember } } as const;
  test.each([
    {
      shape: "a top-level text field", record: { handle: "abc" }, saved: { handle: "abcd" },
      fields: () => deserializeFormSpec({ type: "object", properties: handleMember }, defaultWidgets),
    },
    {
      shape: "a member of an object field", record: { retry: { handle: "abc" } }, saved: { retry: { handle: "abcd" } },
      fields: () => deserializeFormSpec({ type: "object", properties: retryMember }, defaultWidgets),
    },
    {
      shape: "a member of an object field in a JSON root", record: { config: { retry: { handle: "abc" } } },
      saved: { config: { retry: { handle: "abcd" } } }, fields: () => configFields(retryMember),
    },
    {
      shape: "a member named `type`", record: { retry: { type: "abc" } }, saved: { retry: { type: "abcd" } },
      fields: () => deserializeFormSpec({ type: "object", properties: {
        retry: { ...retryMember.retry, properties: { type: handleMember.handle } },
      } }, defaultWidgets),
    },
  ])("after a blocked save, $shape keeps its message through an edit that still fails, and saves once corrected", async ({ record, saved, fields }) => {
    sdkMocks.record = { id: "note-1", ...record };
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields()} />);
    const rule = "Enter at least 3 characters.";
    const handle = () => screen.getByLabelText("Handle") as HTMLInputElement;
    const save = () => screen.getByRole("button", { name: "Save" });
    const messageUnder = (label: string) => screen.getByText(label).closest('[data-layout="stack"]')?.textContent ?? "";
    // The change's validation runs after the event; a macrotask lets it settle before asserting what stays.
    const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    await waitFor(() => expect(handle().value).toBe("abc"));
    fireEvent.change(handle(), { target: { value: "ab" } });
    fireEvent.blur(handle());
    fireEvent.click(save());
    expect(await screen.findByText(rule)).toBeTruthy();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();

    fireEvent.change(handle(), { target: { value: "a" } });
    await settle();
    expect(messageUnder("Handle")).toContain(rule);
    expect(handle().getAttribute("aria-invalid")).toBe("true");
    fireEvent.click(save());
    await settle();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();

    fireEvent.change(handle(), { target: { value: "abcd" } });
    fireEvent.blur(handle());
    await waitFor(() => expect(screen.queryByText(rule)).toBeNull());
    fireEvent.click(save());
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ id: "note-1", ...saved });
  });

  test("a hidden JSON root beside its dotted member holds no error once the member is corrected, and the form saves", async () => {
    sdkMocks.record = { id: "note-1", config: { timeout: 30 } };
    // What the form's error state holds at the root, read inside the form.
    function ConfigErrorState() {
      const { errors } = useFormState();
      return <output>{errors.config ? "config has an error" : "config has no error"}</output>;
    }
    renderWithProviders(<FormView resource="notes.Note" id="note-1"
      fields={[{ name: "config", widget: "json", hidden: true }, ...configFields({ timeout: timeoutMember })]}
      formExtras={() => <ConfigErrorState />} />);
    const timeout = () => screen.getByLabelText("Timeout") as HTMLInputElement;
    const save = () => screen.getByRole("button", { name: "Save" });
    const rule = "Enter a valid whole number.";
    await waitFor(() => expect(timeout().value).toBe("30"));
    fireEvent.change(timeout(), { target: { value: "" } });
    fireEvent.blur(timeout());
    fireEvent.click(save());
    expect(await screen.findByText(rule)).toBeTruthy();
    expect(screen.getByText("config has an error")).toBeTruthy();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();

    fireEvent.change(timeout(), { target: { value: "7" } });
    fireEvent.blur(timeout());
    await waitFor(() => expect(screen.queryByText(rule)).toBeNull());
    await waitFor(() => expect(screen.getByText("config has no error")).toBeTruthy());
    fireEvent.click(save());
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate.mock.calls[0]![0].data).toStrictEqual({ id: "note-1", config: { timeout: 7 } });
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
        <Action id="archive" label="Archive" placement="menu" set={{ status: "ARCHIVED" }} />
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
    // The original and its variant may reuse a tab id: only one reaches the row, as its one pane.
    expect(screen.getByRole("heading", { name: kind === "WHATSAPP" ? "WhatsApp sync" : "Sync" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: kind === "WHATSAPP" ? "Sync" : "WhatsApp sync" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reindex IMAP" })).toBeNull();
    if (kind === "IMAP") {
      fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
      expect(await screen.findByRole("menuitem", { name: "Reindex IMAP" })).toBeTruthy();
    } else {
      expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
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
      implMetadata({ permissions: { name: "permissions", kind: "list", scalar: "String" } }),
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

  test.each([true, undefined])("places a statusbar once to the title's right, without a label or body copy (status=%s)", async (status) => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field name="status" label="Status" widget="statusbar" status={status} options={statusOptions} />
      <Group label="Details"><Field name="wordCount" label="Words" /></Group>
      <Field name="body" label="Lead body" body />
    </FormView>);
    const title = await screen.findByDisplayValue("First");
    const header = title.closest("header")!;
    const steps = within(header).getByRole("list");
    expect(screen.getAllByRole("list")).toEqual([steps]);
    expect(title.compareDocumentPosition(steps) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The statusbar sits at the end of the title row, right-aligned, and the title keeps its width.
    const titleRow = title.closest<HTMLElement>("[data-record-title-row]")!;
    expect(titleRow.contains(steps)).toBe(true);
    expect(steps.closest<HTMLElement>(".justify-end")).not.toBeNull();
    expect(titleRow.className).toContain("flex-wrap");
    expect(screen.queryByText("Status")).toBeNull();
    expect(screen.queryByLabelText("Status")).toBeNull();
    expect(screen.getByText("Lead body").compareDocumentPosition(screen.getByText("Details")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test.each(["field", "form", "resolve"] as const)("a grouped statusbar in the header keeps %s read-only policy", async (lock) => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly={lock === "form"}>
      <Field name="title" label="Title" title />
      <Group label="Details">
        <Field name="status" label="Status" widget="statusbar" options={statusOptions}
          readOnly={lock === "field"} resolve={lock === "resolve" ? (values) => ({ readOnly: values.wordCount === 3 }) : undefined} />
        <Field name="wordCount" label="Words" />
      </Group>
    </FormView>);
    await screen.findByRole("button", { name: "Draft" });
    const header = document.querySelector("header")!;
    expect(within(header).getByRole("list")).toBe(screen.getByRole("list"));
    expect(screen.queryByText("Status")).toBeNull();
    const draft = within(header).getByRole("button", { name: "Draft" });
    await waitFor(() => expect(draft.hasAttribute("disabled")).toBe(true));
    fireEvent.click(draft);
    expect(within(header).getByText("Active").closest("[role='listitem']")?.getAttribute("aria-current")).toBe("step");
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("a header statusbar retains its field mark and reveal action", async () => {
    const onReveal = vi.fn();
    const marks = [{ model: "notes.Note", id: "note-1", marks: [
      { field: "status", label: "Unconfirmed", tone: "warning" as const, onReveal },
    ] }];
    function Marks() { useRecordFieldMarks(marks); return null; }
    renderWithProviders(<RecordFieldMarksProvider><Marks /><FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field name="status" label="Status" widget="statusbar" readOnly options={statusOptions} />
    </FormView></RecordFieldMarksProvider>);
    await screen.findByDisplayValue("First");
    const mark = within(document.querySelector("header")!).getByRole("button", { name: "Unconfirmed: Status" });
    expect(mark.parentElement?.contains(screen.getByRole("list"))).toBe(true);
    fireEvent.click(mark);
    expect(onReveal).toHaveBeenCalledOnce();
  });

  test("a form without a statusbar keeps its status field labelled in the body", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field name="status" label="Status" />
    </FormView>);
    const title = await screen.findByDisplayValue("First");
    const header = title.closest("header")!;
    const status = screen.getByLabelText("Status");
    expect(header.contains(status)).toBe(false);
    expect(screen.getByText("Status")).toBeTruthy();
  });

  test("a conditional statusbar stays in the header as its visibility changes", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1">
      <Field name="title" label="Title" title />
      <Field name="status" label="Status" widget="statusbar" options={statusOptions} showWhen={(values) => values.title === "First"} />
    </FormView>);
    const title = await screen.findByDisplayValue("First");
    const header = title.closest("header")!;
    expect(screen.getByRole("list").closest("header")).toBe(header);
    fireEvent.change(title, { target: { value: "Hidden" } });
    await waitFor(() => expect(screen.queryByRole("list")).toBeNull());
    expect(screen.queryByText("Status")).toBeNull();
    fireEvent.change(title, { target: { value: "First" } });
    expect(screen.getByRole("list").closest("header")).toBe(header);
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
    // The predicate also receives the form's data resource, so a kind-level tab can gate on it.
    await waitFor(() => expect(visibleWhen).toHaveBeenCalledWith(
      expect.objectContaining({ title }), expect.objectContaining({ modelLabel: resource }),
    ));
    expect(sdkMocks.recordSelection).toContain("title");
    expect(screen.queryAllByRole("heading", { name: "Related" })).toHaveLength(visible ? 1 : 0);
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

    // The record's one pane renders beneath the sheet under its heading and badge.
    expect(await screen.findByText("Pane for note-1")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByRole("heading", { name: "Pane" }).parentElement?.textContent).toBe("Pane7");
  });

  test("rejects a contributed record tab that claims the editable lines' pane id", async () => {
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
              "notes.lines": {
                content: (
                  <Tab id="editable-lines" label="Shadow lines">
                    <p>contributed</p>
                  </Tab>
                ),
              },
            },
          }),
        ),
      ).toThrow(/duplicate record tab id "editable-lines"/);
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
    expect(screen.queryByRole("combobox", { name: "Reviewer" })).toBeNull();
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
      expect(screen.getByRole("combobox", { name: /Reviewer/ }).textContent).toContain(
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

  test("mounts an inactive pane lazily and unmounts it when another pane opens", async () => {
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
        recordTabs={[
          { id: "notes", label: "Notes", render: () => <p>Notes panel</p> },
          { id: "activity", label: "Activity", render: renderPanel },
        ]}
      />,
    );

    expect(await screen.findByText("Notes panel")).toBeTruthy();
    // An inactive pane's factory is not even called; its component mounts when the pane opens.
    expect(renderPanel).not.toHaveBeenCalled();
    expect(mountPanel).not.toHaveBeenCalled();
    expect(screen.queryByText("Activity panel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(await screen.findByText("Activity panel")).toBeTruthy();
    expect(mountPanel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(screen.getByLabelText("Title")).toBeTruthy();
    // Base UI unmounts the outgoing panel when its exit transition completes.
    await waitFor(() => {
      expect(screen.queryByText("Activity panel")).toBeNull();
      expect(unmountPanel).toHaveBeenCalledTimes(1);
    });
  });

  test("workspace records open the validated default pane beneath the sheet", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields.map((field) => field.name === "status" ? { ...field, status: undefined } : field)}
        recordPresentation="workspace"
        defaultRecordTab="editor"
        recordExtras={() => <p>Related records</p>}
        recordTabs={[
          { id: "runs", label: "Runs", render: () => <p>Runs panel</p> },
          { id: "editor", label: "Editor", keepMounted: true, render: ({ active }) => <>
            <button type="button">Editor action</button><output data-testid="retained-panel-active">{String(active)}</output>
          </> },
        ]}
      />,
    );

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Runs", "Editor"]);
    expect(screen.getByRole("tab", { name: "Editor" }).getAttribute("aria-selected")).toBe("true");
    // The tab strip mounts before the asynchronous record read permits pane content.
    expect(await screen.findByRole("button", { name: "Editor action" })).toBeTruthy();
    expect(screen.getByTestId("retained-panel-active").textContent).toBe("true");
    expect(await screen.findByText("Active")).toBeTruthy();
    expect(screen.queryByText("ACTIVE")).toBeNull();
    // The sheet and the record's extras stay with every pane.
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    expect(screen.getByText("Related records")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    expect(await screen.findByText("Runs panel")).toBeTruthy();
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Editor action" })).toBeNull();
    expect(screen.getByTestId("retained-panel-active").textContent).toBe("false");
  });

  test("the rail sits beside the sheet, never beside a pane", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields}
      recordPresentation="workspace" defaultRecordTab="editor"
      recordTabs={[{ id: "editor", label: "Editor", keepMounted: true,
        render: () => <div data-testid="workspace-canvas" className="h-full">Canvas</div> }]}
    />, undefined, undefined, formChildren({ "notes.Note#rail": { "notes.properties": {
      content: <FormView.RailGroup id="properties" label="Properties" content={<p>Summary</p>} />,
    } } }));

    const canvas = await screen.findByTestId("workspace-canvas");
    const rail = screen.getByRole("complementary");
    expect(within(rail).getByText("Properties")).toBeTruthy();
    expect(rail.closest("form")).toBeTruthy();
    expect(canvas.closest("aside")).toBeNull();
    expect(rail.compareDocumentPosition(canvas) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelectorAll("aside")).toHaveLength(1);
  });

  test("full-bleed and document panes share the header and strip geometry while retaining the editor draft", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} defaultRecordTab="editor"
      recordTabs={[
        { id: "editor", label: "Editor", presentation: "full-bleed", keepMounted: true,
          render: () => <input aria-label="Editor draft" defaultValue="Original" /> },
        { id: "activity", label: "Activity", render: () => <p>Activity content</p> },
      ]} />);
    const editor = await screen.findByRole("tabpanel", { name: "Editor" });
    const draft = await within(editor).findByRole("textbox", { name: "Editor draft" });
    fireEvent.change(draft, { target: { value: "Unsaved draft" } });
    expect(editor.className).toContain("flex-1");
    expect(editor.className).toContain("overflow-hidden");
    expect(editor.parentElement?.className).toContain("h-full");
    expect(editor.className).not.toContain("max-w-[1100px]");
    expect(screen.getByLabelText("Reminder").closest(".overflow-auto")?.className).toContain("max-h-[50%]");
    const recordChrome = () => {
      const title = screen.getByRole("textbox", { name: "Title" });
      const header = title.closest("header")!;
      const strip = screen.getByRole("tablist");
      expect(title.parentElement!.className).toContain("text-22");
      expect(title.style.font).toBe("inherit");
      return { headerClasses: header.parentElement!.className, stripClasses: strip.className };
    };
    const chrome = recordChrome();
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    const activity = await screen.findByRole("tabpanel", { name: "Activity" });
    expect(activity.className).toContain("max-w-[1100px]");
    // The pane follows the form, so the whole record fills the content area, never the form alone.
    expect(activity.closest("form")).toBeNull();
    expect(document.querySelector("form")?.className ?? "").not.toContain("min-h-full");
    expect(activity.parentElement?.className).toContain("min-h-full");
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    expect(recordChrome()).toEqual(chrome);
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    expect(recordChrome()).toEqual(chrome);
    expect(screen.getByRole("textbox", { name: "Editor draft" })).toBe(draft);
    expect(draft).toHaveProperty("value", "Unsaved draft");
  });

  test("workspace records without panes keep the compact header and scroll their sheet", async () => {
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
    expect(heading.className).toContain("text-15");
    expect(heading.className).not.toContain("text-22");
    expect(within(heading.closest("header")!).getByText("Active").className).toContain("justify-self-start");
    expect(screen.getAllByText("Active")).toHaveLength(1);
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByText("Draft")).toBeNull();
    expect(screen.queryByText("Status")).toBeNull();
    expect(heading.closest("header")?.parentElement?.className).toContain("sticky");
    expect(heading.closest("form")?.parentElement?.className).toContain("overflow-auto");
    expect(screen.queryByRole("tab")).toBeNull();
  });

  test.each(["run", "submit"] as const)("read-only records promote their remaining %s verb after omitting set actions", async (binding) => {
    const run = vi.fn(async () => undefined);
    const submit = vi.fn(async () => ({ ok: true, message: "Review recorded." }));
    renderWithProviders(<FormView resource="notes.Note" id="note-1" readOnly>
      <Field name="title" label="Title" title />
      <Action id="review" label="Review" submitLabel="Record review" {...(binding === "run" ? { run } : { submit })} />
      <Action id="rename" label="Rename" set={{ title: "Changed" }} />
    </FormView>);
    expect(await screen.findByRole("heading", { name: "First" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Rename" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
    const review = screen.getByRole("button", { name: "Review" });
    expect(review.classList.contains("bg-inset")).toBe(true);
    act(() => review.focus());
    fireEvent.click(review);
    if (binding === "submit") {
      const dialog = await screen.findByRole("dialog", { name: "Review" });
      await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
      fireEvent.click(within(dialog).getByRole("button", { name: "Record review" }));
      await waitFor(() => expect(submit).toHaveBeenCalledWith(
        {}, expect.objectContaining({ record: sdkMocks.record }), expect.objectContaining({ errorNotification: false }),
      ));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Review" })).toBeNull());
      await waitFor(() => expect(document.activeElement).toBe(review));
      expect(run).not.toHaveBeenCalled();
    } else {
      await waitFor(() => expect(run).toHaveBeenCalledOnce());
      expect(submit).not.toHaveBeenCalled();
    }
  });

  test("a title-placed field wraps below the title instead of squeezing it to a sliver", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={[
          ...fields.filter((field) => field.name !== "wordCount"),
          { name: "wordCount", label: "Word Count", readOnly: true, placement: "title" },
        ]}
      />,
    );
    const title = await screen.findByRole("textbox", { name: "Title" });
    const row = title.closest<HTMLElement>("[data-record-title-row]")!;
    // The row wraps, and the title keeps a readable basis rather than shrinking to its minimum.
    expect(row.className).toContain("flex-wrap");
    expect([...row.children][0]?.className).toContain("flex-[1_1_16rem]");
    expect(row.children.length).toBeGreaterThan(1);
  });

  test("document records keep the sheet above their panes, all one sheet", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        defaultRecordTab="activity"
        recordTabs={[
          { id: "notes", label: "Notes", render: () => <p>Notes panel</p> },
          { id: "activity", label: "Activity", render: () => <p>Activity panel</p> },
        ]}
      />,
    );

    expect((await screen.findAllByRole("tab")).map((tab) => tab.textContent)).toEqual(["Notes", "Activity"]);
    expect(await screen.findByText("Activity panel")).toBeTruthy();
    expect(screen.getByLabelText("Reminder")).toBeTruthy();
    // The pane follows the form: the record fills the content area as one sheet, so a short
    // header never stretches over the pane and pushes it below the fold.
    const record = screen.getByRole("tabpanel", { name: "Activity" }).parentElement!;
    expect(record.className).toContain("min-h-full");
    expect(record.className).toContain("bg-sheet");
    expect(document.querySelector("form")?.className ?? "").not.toContain("min-h-full");
  });

  test("a record without panes fills the content area with its sheet", async () => {
    renderWithProviders(<FormView resource="notes.Note" id="note-1" fields={fields} />);
    const title = await screen.findByRole("textbox", { name: "Title" });
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(title.closest("form")?.parentElement?.className).toContain("min-h-full");
  });

  test("workspace pane switches keep dirty sheet fields and reset to the default for a new record", async () => {
    function Harness(): ReactElement {
      const [id, setId] = useState("note-1");
      return <>
        <button type="button" onClick={() => setId("note-2")}>Next note</button>
        <FormView resource="notes.Note" id={id} fields={fields}
          recordPresentation="workspace" defaultRecordTab="editor"
          recordTabs={[
            { id: "notes", label: "Notes", render: () => <p>Notes panel</p> },
            { id: "editor", label: "Editor", render: () => <p>Editor panel</p> },
          ]} />
      </>;
    }
    renderWithProviders(<Harness />);

    expect(await screen.findByText("Editor panel")).toBeTruthy();
    const title = await screen.findByLabelText("Title");
    fireEvent.change(title, { target: { value: "Dirty title" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(await screen.findByText("Notes panel")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    expect(screen.getByLabelText("Title")).toBe(title);
    expect((title as HTMLInputElement).value).toBe("Dirty title");

    sdkMocks.record = { ...sdkMocks.record, id: "note-2", title: "Second" };
    fireEvent.click(screen.getByRole("button", { name: "Next note" }));
    expect(await screen.findByText("Editor panel")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Editor" }).getAttribute("aria-selected")).toBe("true");
  });

  test("workspace falls back to the first pane for an invalid default, and create shows none", async () => {
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1" fields={fields}
        recordPresentation="workspace" defaultRecordTab="removed"
        recordTabs={[
          { id: "editor", label: "Editor", render: () => <p>Editor panel</p> },
          { id: "runs", label: "Runs", render: () => <p>Runs panel</p> },
        ]} />,
    );
    expect(await screen.findByText("Editor panel")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Editor" }).getAttribute("aria-selected")).toBe("true");

    cleanup();
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note" id={null} fields={fields}
        recordPresentation="workspace" defaultRecordTab="editor"
        recordTabs={[{ id: "editor", label: "Editor", render: () => <p>Editor panel</p> }]} />,
    );
    expect(await screen.findByLabelText("Title")).toBeTruthy();
    expect(screen.queryByText("Editor panel")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Editor" })).toBeNull();
  });

  test("resolves record-dependent read-only fields in the header, body, and groups", async () => {
    const resolvedFields = [
      {
        name: "title",
        label: "Title",
        title: true,
        resolve: (record: Row) => ({
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
          readOnly: record.status === "PUBLISHED",
        }),
      },
      { name: "status", label: "Status", defaultValue: "DRAFT" },
      {
        name: "wordCount",
        label: "Word Count",
        resolve: (record: Row) => ({
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
      readOnly: record.status === "PUBLISHED",
    });
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field
          name="title"
          title
          resolve={(record) => ({
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

  test("blocks create and flags a missing required field in a later sheet section", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockClear();
    const metadata: TestSchemaMetadata = {
      types: {
        NoteType: {
          ...defaultModel("NoteType", "notes.Note"),
          fields: {
            title: { name: "title", kind: "scalar", scalar: "String" },
            summary: { name: "summary", kind: "scalar", scalar: "String" },
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
      <FormView resource="notes.Note">
        <Group label="Identity">
          <Field name="title" label="Title" />
          <Field name="summary" label="Summary" />
        </Group>
        <Group label="Schedule">
          <Field name="deadline" label="Deadline" />
        </Group>
      </FormView>,
      metadata,
    );
    fireEvent.keyDown(screen.getByLabelText("Title"), { key: "Enter" });

    // Every section is on the sheet, so the required field shows its error in place.
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
    const banner = screen.getByText("environment: This field cannot be blank.").closest<HTMLElement>('[role="alert"]')!;
    expect(within(banner).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Please fix the highlighted fields: Title.", "environment: This field cannot be blank.",
    ]);
  });
});

/** The form's containers with no children, as a composed app declares them. */
const NO_FORM_CHILDREN = containersFromChildren(FORM_CONTAINERS, {});

/**
 * Reports, once its record has loaded (at once on a create), the sections a form's surface holds:
 * the sheet's then the panes', each as its key, label, sequence and pane.
 */
function SectionsProbe(props: UseFormViewSurfaceProps & { seen: (sections: unknown[]) => void }): ReactElement {
  // The probe is a second surface of the form beside it: it names a draft of its own.
  return <DraftScopeProvider scope="sections-probe"><SectionsProbeSurface {...props} /></DraftScopeProvider>;
}

function SectionsProbeSurface({ seen, ...props }: UseFormViewSurfaceProps & { seen: (sections: unknown[]) => void }): null {
  const surface = useFormViewSurface(props);
  const sections = [...surface.sections, ...surface.paneSections]
    .map((section) => [section.key, section.label, section.sequence, section.pane]);
  const loaded = props.id == null || surface.displayRecord != null;
  useEffect(() => { if (loaded) seen(sections); });
  return null;
}

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

function recordActionsMetadata(overrides: Partial<DataResourceMetadata> = {}): SchemaFieldMetadata {
  return schemaFieldMetadataFromDataResources([testDataResource("notes.Note", {
    resourceType: "notes/note",
    fields: [
      { name: "title", kind: "scalar", scalar: "String", readable: true, creatable: true,
        updatable: true, aggregatable: false, requiredOnCreate: false },
      { name: "is_archived", kind: "scalar", scalar: "Boolean", readable: true, creatable: true,
        updatable: true, aggregatable: false, requiredOnCreate: false, archivable: true },
      { name: "is_trashed", kind: "scalar", scalar: "Boolean", readable: true, creatable: false,
        updatable: false, aggregatable: false, requiredOnCreate: false, trashable: true },
      { name: "permissions", kind: "list", scalar: "String", readable: true, creatable: false,
        updatable: false, aggregatable: false, requiredOnCreate: false },
    ],
    ...overrides,
  })]);
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

/** A team whose `members` is a to-many relation to users represented by name. */
function teamMetadata(): TestSchemaMetadata {
  return {
    types: {
      TeamType: {
        ...defaultModel("TeamType", "teams.Team"),
        fields: {
          name: { name: "name", kind: "scalar", scalar: "String" },
          members: { name: "members", kind: "list", scalar: null, relationModelLabel: "iam.User" },
        },
      },
      UserType: {
        ...defaultModel("UserType", "iam.User"),
        fields: { display_name: { name: "display_name", kind: "scalar", scalar: "String" } },
        resource: { ...defaultResource("UserType", "iam.User"), recordRepresentation: "display_name" },
      },
    },
  };
}

function factsMetadata(): TestSchemaMetadata {
  const note = defaultModel("NoteType", "notes.Note");
  return {
    types: {
      NoteType: {
        ...note,
        fields: {
          title: { name: "title", kind: "scalar", scalar: "String" },
          amount: { name: "amount", kind: "scalar", scalar: "Decimal", widget: "money", currencyField: "currency" },
          currency: { name: "currency", kind: "relation", relationModelLabel: "money.Currency", relationObject: true },
          due_on: { name: "due_on", kind: "scalar", scalar: "Date", widget: "date" },
          created_by: { name: "created_by", kind: "scalar", scalar: "ID" },
          created_by_label: { name: "created_by_label", kind: "scalar", scalar: "String" },
          updated_by: { name: "updated_by", kind: "scalar", scalar: "ID" },
          updated_by_label: { name: "updated_by_label", kind: "scalar", scalar: "String" },
          created_at: { name: "created_at", kind: "scalar", scalar: "DateTime" },
          updated_at: { name: "updated_at", kind: "scalar", scalar: "DateTime" },
        },
        resource: {
          ...note.resource,
          recordRepresentation: "title",
          contextFields: ["amount", "due_on"],
          subtitle: { created: "created_at", updated: "updated_at" },
        },
      },
      CurrencyType: {
        ...defaultModel("CurrencyType", "money.Currency"),
        fields: { code: { name: "code", kind: "scalar", scalar: "String" } },
        resource: { ...defaultResource("CurrencyType", "money.Currency"), recordRepresentation: "code" },
      },
    },
  };
}

/** Open a select by its accessible name once its options load, and pick one option. */
/** Search a to-many chips field for a related record and pick it. */
async function pickRelated(label: string, option: string): Promise<void> {
  const search = await screen.findByRole("combobox", { name: label });
  fireEvent.input(search, { target: { value: option }, inputType: "insertText" });
  const item = await screen.findByRole("option", { name: option });
  fireEvent.pointerDown(item, { pointerType: "mouse" });
  fireEvent.click(item);
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
