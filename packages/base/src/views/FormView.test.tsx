// @vitest-environment happy-dom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
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
  ModelMetadataProvider,
  type AppRuntime,
  type Row,
  type SchemaFieldMetadata,
} from "@angee/sdk";
import { useMemo, useState, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { defaultWidgets } from "../widgets";
import { Form } from "./Form";
import { FormView, formViewSectionsSlot, type FormField } from "./FormView";
import {
  Action,
  Field,
  Group,
} from "./page";

const sdkMocks = vi.hoisted(() => ({
  record: null as Row | null,
  mutate: vi.fn(),
  recordSelection: undefined as readonly string[] | undefined,
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useResourceRecord: (
      _model: string,
      _id: string | null,
      options?: { fields?: readonly string[] },
    ) => {
      sdkMocks.recordSelection = options?.fields;
      return {
        record: sdkMocks.record,
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useResourceMutation: () => [
      sdkMocks.mutate,
      { fetching: false, error: null },
    ],
  };
});

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
    sdkMocks.recordSelection = undefined;
    sdkMocks.mutate.mockImplementation(async ({ data }: { data: Row }) => ({
      ...sdkMocks.record,
      ...data,
    }));
  });

  test("throws when fields prop and field children are both declared", () => {
    expect(() =>
      renderWithProviders(
        <FormView model="notes.Note" id="note-1" fields={fields}>
          <Field name="title" />
        </FormView>,
      ),
    ).toThrow(/cannot mix the fields\/groups props with element children/);
  });

  test("throws when groups prop and Group children are both declared", () => {
    expect(() =>
      renderWithProviders(
        <FormView
          model="notes.Note"
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

  test("renders declared record actions in the action menu", async () => {
    renderWithProviders(
      <FormView model="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="archive" label="Archive" set={{ status: "ARCHIVED" }} />
      </FormView>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    await screen.findByRole("menuitem", { name: "Archive" });
  });

  test("hides the record delete action when its predicate does not match", async () => {
    const deleteAction = {
      canDelete: true,
      isPending: false,
      onDelete: vi.fn(),
    };

    renderWithProviders(
      <FormView
        model="notes.Note"
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
        model="notes.Note"
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

  test("renders record-aware toolbarStart content", async () => {
    renderWithProviders(
      <FormView
        model="notes.Note"
        id="note-1"
        toolbarStart={({ record }) =>
          record?.status === "ACTIVE" ? (
            <button type="button">Provision</button>
          ) : null
        }
      >
        <Field name="title" label="Title" title />
        <Field name="status" label="Status" />
      </FormView>,
    );

    expect(await screen.findByRole("button", { name: "Provision" })).toBeTruthy();
  });

  test("lets toolbarStart patch displayed record state immediately", async () => {
    renderWithProviders(
      <FormView
        model="notes.Note"
        id="note-1"
        fields={fields}
        toolbarStart={({ patchRecord, record }) =>
          record?.status === "ACTIVE" ? (
            <button type="button" onClick={() => patchRecord({ status: "ARCHIVED" })}>
              Mark archived
            </button>
          ) : null
        }
      />,
    );

    expect(statusStep("Active")?.getAttribute("aria-current")).toBe("step");

    fireEvent.click(screen.getByRole("button", { name: "Mark archived" }));

    await waitFor(() =>
      expect(statusStep("Archived")?.getAttribute("aria-current")).toBe("step"),
    );
    expect(screen.queryByRole("button", { name: "Mark archived" })).toBeNull();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("runs a declarative set action through the update mutation", async () => {
    renderWithProviders(
      <FormView model="notes.Note" id="note-1">
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
      <FormView model="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
        <Action id="sync" label="Sync" run={run} />
      </FormView>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Sync" }));

    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(run.mock.calls[0]?.[0]).toMatchObject({ record: { id: "note-1" } });
  });

  test("re-seeds when the record reference changes for the same id", async () => {
    // A refetch (e.g. after a run action) lands a fresh record object under the
    // same id; the form must reflect it without a stale render consuming it.
    function Harness(): ReactElement {
      const [version, setVersion] = useState(0);
      return (
        <>
          <button
            type="button"
            onClick={() => {
              sdkMocks.record = { ...sdkMocks.record, id: "note-1", title: "Refetched" };
              setVersion((current) => current + 1);
            }}
          >
            refetch {version}
          </button>
          <FormView model="notes.Note" id="note-1" fields={fields} />
        </>
      );
    }

    renderWithProviders(<Harness />);
    const title = await screen.findByLabelText("Title");
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("First"),
    );

    fireEvent.click(screen.getByRole("button", { name: /refetch/ }));

    await waitFor(() =>
      expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
        "Refetched",
      ),
    );
  });

  test("renders standalone Form from Field and Group children", async () => {
    renderWithProviders(
      <Form model="notes.Note" id="note-1">
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
      <Form model="notes.Note" id="note-1" layout="tabs">
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

  test("derives a slug from the header title field while creating", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView
        model="notes.Note"
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
        model="notes.Note"
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
            model="notes.Note"
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
        model="notes.Note"
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

  test("omits unselected option fields from create payloads", async () => {
    renderForm(null);

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", reminderAt: null },
    });
  });

  test("omits blank numeric fields from create payloads", async () => {
    renderWithProviders(
      <FormView
        model="agents.InferenceModel"
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

  test("merges default values into create payloads", async () => {
    renderWithProviders(
      <FormView
        model="notes.Note"
        fields={fields}
        defaultValues={{ status: "ACTIVE" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "", status: "ACTIVE", reminderAt: null },
    });
  });

  test("overwrites pre-seeded sibling fields from impl prefill only while creating", async () => {
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
        model="OAuthClient"
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
    sdkMocks.mutate.mockReset();
    renderWithProviders(
      <FormView model="OAuthClient" id="client-1" fields={implFields} />,
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
      data: { providerType: "oidc", id: "client-1" },
    });
  });

  test("submits fields declared through the groups prop", async () => {
    renderWithProviders(
      <FormView
        model="notes.Note"
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

  test("merges FORM_VIEW_SECTIONS_SLOT fields into the submit payload", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView model="notes.Note">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      {
        slots: [
          {
            slot: formViewSectionsSlot("notes.Note"),
            id: "notes.extra",
            content: (
              <Group label="Extra">
                <Field name="slotCode" label="Slot Code" />
              </Group>
            ),
          },
        ],
      },
    );

    expect(screen.getByText("Extra")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Slot Title" },
    });
    fireEvent.change(screen.getByLabelText("Slot Code"), {
      target: { value: "slot-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Slot Title", slotCode: "slot-1" },
    });
  });

  test("reads many2one record ids and writes the flat relation field", async () => {
    sdkMocks.record = {
      id: "client-1",
      displayName: "Acme",
      vendor: { id: "vendor-1", displayName: "Vendor One" },
    };
    const relationFields = [
      { name: "displayName", label: "Display Name", title: true },
      {
        name: "vendor",
        label: "Vendor",
        widget: "many2one",
        options: [
          { value: "vendor-1", label: "Vendor One" },
          { value: "vendor-2", label: "Vendor Two" },
        ],
      },
    ] satisfies readonly FormField[];

    renderWithProviders(
      <FormView
        model="OAuthClient"
        id="client-1"
        fields={relationFields}
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Vendor/ }).textContent).toContain(
        "Vendor One",
      ),
    );
    cleanup();
    sdkMocks.record = null;
    sdkMocks.mutate.mockReset();
    renderWithProviders(
      <FormView
        model="OAuthClient"
        fields={relationFields}
        defaultValues={{ vendor: "vendor-2" }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { displayName: "", vendor: "vendor-2" },
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
            model="notes.Note"
            id="note-1"
            fields={viewFields}
            onSaved={() => setSaveVersion((current) => current + 1)}
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
      return (
        <ModalsHost>
          <ToastProvider>
            <ModelMetadataProvider>
              <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
                <Outlet />
              </AppRuntimeProvider>
            </ModelMetadataProvider>
          </ToastProvider>
        </ModalsHost>
      );
    }

    const rootRoute = createRootRoute({ component: Root });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => (
        <FormView
          model="notes.Note"
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

  test("projects only fields the SDL read type exposes (skips write-only inputs)", async () => {
    // A write-only input (password) is declared on the form but absent from the
    // read type. Selecting it would make the whole detail/return query invalid
    // and the record would load as null (every field blank, "Untitled").
    const metadata: SchemaFieldMetadata = {
      types: {
        UserType: {
          typeName: "UserType",
          recordRepresentation: "username",
          fields: {
            username: { name: "username", kind: "scalar", scalar: "String" },
            email: { name: "email", kind: "scalar", scalar: "String" },
            vendor: {
              name: "vendor",
              kind: "relation",
              relationTarget: "VendorType",
            },
          },
        },
      },
    };
    sdkMocks.record = { id: "user-1", username: "ada", email: "ada@x.io" };

    renderWithProviders(
      <FormView
        model="iam.User"
        id="user-1"
        fields={[
          { name: "username", label: "Username", title: true },
          { name: "email", label: "Email" },
          { name: "vendor", label: "Vendor", widget: "many2one" },
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
    expect(selection).toContain("vendor.id"); // relation → `<field>.id`
    expect(selection).not.toContain("password"); // write-only → never read back
  });

  test("blocks create and flags a missing required field inline", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockReset();
    const metadata: SchemaFieldMetadata = {
      types: {
        NoteType: {
          typeName: "NoteType",
          fields: { code: { name: "code", kind: "scalar", scalar: "String" } },
          rootFields: { create: "createNote", requiredCreateFields: ["code"] },
        },
      },
    };

    renderWithProviders(
      <FormView model="notes.Note" fields={[{ name: "code", label: "Code" }]} />,
      metadata,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    // The required field is flagged inline and the submit never reaches the server.
    await screen.findByText("This field is required.");
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("renders server validation errors under their field and in the banner", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockReset();
    sdkMocks.mutate.mockRejectedValue({
      graphQLErrors: [
        {
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

    renderWithProviders(<FormView model="notes.Note" fields={fields} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    // Field message renders under the Reminder field…
    await screen.findByText("This field cannot be blank.");
    // …and the form-level message stays in the banner.
    expect(screen.getByText("Note is misconfigured.")).toBeTruthy();
  });

  test("renders and submits a field only when its showWhen discriminator matches", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockReset();
    renderWithProviders(
      <FormView model="notes.Note">
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
    sdkMocks.mutate.mockReset();
    renderWithProviders(
      <FormView model="notes.Note">
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
    sdkMocks.mutate.mockReset();
    renderWithProviders(
      <FormView model="Widget">
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
      <FormView model="Widget" id="w-1">
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
    sdkMocks.mutate.mockReset();
    sdkMocks.mutate.mockRejectedValue({
      graphQLErrors: [
        {
          extensions: {
            validationErrors: { title: ["This field cannot be blank."] },
            formErrors: [],
          },
        },
      ],
    });

    renderWithProviders(<FormView model="notes.Note" fields={fields} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    await screen.findByText("This field cannot be blank.");
    // Only field errors → the banner prompts the user to fix highlighted fields.
    expect(screen.getByText("Please fix the highlighted fields.")).toBeTruthy();
  });
});

function renderForm(id: string | null): void {
  renderWithProviders(<FormView model="notes.Note" id={id} fields={fields} />);
}

function renderWithProviders(
  children: ReactElement,
  metadata?: SchemaFieldMetadata,
  forms?: Record<string, unknown>,
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
    <RouterContextProvider router={router}>
      <ModalsHost>
        <ToastProvider>
          <ModelMetadataProvider metadata={metadata}>
            <AppRuntimeProvider
              runtime={{
                widgets: defaultWidgets,
                ...(forms ? { forms } : {}),
                ...runtime,
              }}
            >
              {children}
            </AppRuntimeProvider>
          </ModelMetadataProvider>
        </ToastProvider>
      </ModalsHost>
    </RouterContextProvider>,
  );
}

function cloneFields(source: readonly FormField[]): FormField[] {
  return source.map((field) => ({ ...field }));
}

function statusStep(label: string): Element | null {
  return screen.getByText(label).closest("[role='listitem']");
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
