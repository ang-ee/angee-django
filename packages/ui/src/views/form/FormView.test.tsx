import { testResourceQuery } from "@angee/metadata/testing";
// @vitest-environment happy-dom

import type {
  DataResourceFieldMetadata,
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
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  AppRuntimeProvider,
  type AppRuntime,
  type FormOverrideMap,
  } from "../../runtime";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import { withTestResourceInventory } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import type {
  Row,
} from "@angee/metadata";
import {
  useEffect,
  useMemo,
  useState,
  type ComponentProps,
  type ReactElement,
} from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { defaultWidgets } from "../../widgets";
import { deserializeFormSpec } from "./form-spec";
import { Form } from "./Form";
import {
  FormView,
  FORM_VIEW_RECORD_CHROME_SLOT,
  formViewRecordActionsSlot,
  formViewSectionsSlot,
  type FormField,
  type FormSubmitContext,
} from "./FormView";
import { useRecordChromeContext } from "../resource/record-chrome-context";
import {
  Action,
  Field,
  Group,
  Tab,
} from "../page";

const sdkMocks = vi.hoisted(() => ({
  record: null as Row | null,
  listRows: [] as Row[],
  // Whether the most recent relation-options `useList` ran with its query
  // enabled — the deferred 200-row fetch fires only once the picker is opened,
  // so this stays `false` on a read-only/show render and an editable mount.
  listEnabled: false,
  mutate: vi.fn(),
  // The F6 `<resource>_save` diff-apply owner, mocked so a lines form asserts the
  // routing ({pk, patch, lines}) without a live custom-mutation transport.
  save: vi.fn(),
  recordSelection: undefined as readonly string[] | undefined,
  // When set, the record read answers with only the paths the view selected —
  // what a GraphQL detail query actually returns. Opt-in, so the tests that stub a
  // whole row and assert on it keep reading it whole; a test asserting that
  // FormView *selects* what it reads turns it on.
  projectToSelection: false,
  mutationAction: undefined as string | undefined,
  mutationOptions: undefined as { fields?: readonly string[]; enabled?: boolean } | undefined,
}));

type TestSchemaMetadata = Pick<SchemaFieldMetadata, "types"> &
  Partial<Pick<SchemaFieldMetadata, "labels" | "resources">>;

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeResourceSave: () => ({
      save: sdkMocks.save,
      fetching: false,
      error: null,
      reset: vi.fn(),
    }),
  };
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  const fieldsFromMeta = (meta: unknown): readonly string[] | undefined => {
    const fields = (meta as { fields?: unknown } | undefined)?.fields;
    if (!Array.isArray(fields)) return undefined;
    const paths: string[] = [];
    const visit = (items: readonly unknown[], prefix = ""): void => {
      for (const item of items) {
        if (typeof item === "string") {
          paths.push(prefix ? `${prefix}.${item}` : item);
          continue;
        }
        if (!item || typeof item !== "object") continue;
        for (const [key, value] of Object.entries(item)) {
          if (Array.isArray(value)) visit(value, prefix ? `${prefix}.${key}` : key);
        }
      }
    };
    visit(fields);
    return paths;
  };
  const mutationResult = (
    action: "create" | "update",
    mutateAsync: (input: { id?: string | number; values?: Record<string, unknown> }) => Promise<{ data: Row | null }>,
  ) => (options?: { meta?: unknown }) => {
    sdkMocks.mutationAction = action;
    sdkMocks.mutationOptions = {
      fields: fieldsFromMeta(options?.meta),
      enabled: true,
    };
    return {
      mutateAsync,
      mutation: { isPending: false, error: null },
    };
  };
  // The read answers with the columns the caller selected and nothing else — the
  // server-side projection this harness otherwise skips. Gated on
  // `projectToSelection`; see the flag's note.
  const projectedRecord = (selection: readonly string[] | undefined) => {
    const record = sdkMocks.record ?? undefined;
    if (!record || !sdkMocks.projectToSelection) return record;
    const selected = new Set((selection ?? []).map((path) => path.split(".")[0]));
    return Object.fromEntries(
      Object.entries(record).filter(([name]) => selected.has(name)),
    ) as Row;
  };
  return {
    ...actual,
    // The lines save path invalidates the resource caches after a custom-mutation
    // write (no Refine provider in this harness); a no-op keeps every form render safe.
    useInvalidate: () => vi.fn(async () => undefined),
    useOne: (options?: { meta?: unknown; queryOptions?: { enabled?: boolean } }) => {
      sdkMocks.recordSelection = fieldsFromMeta(options?.meta);
      return {
        result: options?.queryOptions?.enabled === false ? undefined : projectedRecord(sdkMocks.recordSelection),
        query: {
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
      };
    },
    useList: (options?: { queryOptions?: { enabled?: boolean } }) => {
      // The relation-options query is deferred via refine's `queryOptions.enabled`;
      // when disabled it returns no rows and never fires — mirror that so a test
      // can prove the read path does not pull the 200-row option list.
      const enabled = options?.queryOptions?.enabled !== false;
      sdkMocks.listEnabled = enabled;
      return {
        result: enabled
          ? { data: sdkMocks.listRows, total: sdkMocks.listRows.length }
          : { data: [], total: 0 },
        query: {
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
      };
    },
    useCreate: mutationResult("create", async ({ values = {} }) => ({
      data: await sdkMocks.mutate({ data: values }),
    })),
    useUpdate: mutationResult("update", async ({ id, values = {} }) => ({
      data: await sdkMocks.mutate({ data: { ...values, id } }),
    })),
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

describe("FormView sidebar layout", () => {
  afterEach(() => {
    cleanup();
  });

  const sidebarGroups = [
    {
      label: "Properties",
      placement: "properties" as const,
      fields: [{ name: "reminderAt", label: "Reminder", widget: "datetime" }],
      actions: [],
    },
    {
      label: "Detail",
      fields: [{ name: "wordCount", label: "Word Count", readOnly: true }],
      actions: [],
    },
  ];

  function renderSidebar(layout: "sidebar" | "tabs"): HTMLElement | null {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        layout={layout}
        fields={fields}
        groups={sidebarGroups}
      />,
    );
    return document.querySelector(".form-sidebar-grid");
  }

  test("puts a group marked properties in the standing column, and the rest in the body", () => {
    const grid = renderSidebar("sidebar");
    expect(grid).not.toBeNull();
    const column = grid!.querySelector("aside");
    expect(column).not.toBeNull();
    // The marked group is in the column...
    expect(column!.textContent).toContain("Properties");
    // ...and the unmarked one is not.
    expect(column!.textContent).not.toContain("Detail");
    expect(grid!.firstElementChild!.textContent).toContain("Detail");
  });

  test("leaves every other layout alone", () => {
    // A donor addon marks its group once; a host form that did not ask for the
    // column must render it as an ordinary section rather than losing it.
    expect(renderSidebar("tabs")).toBeNull();
    expect(document.body.textContent).toContain("Properties");
  });
});

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
    sdkMocks.save.mockReset();
    sdkMocks.listRows = [];
    sdkMocks.listEnabled = false;
    sdkMocks.recordSelection = undefined;
    sdkMocks.projectToSelection = false;
    sdkMocks.mutationAction = undefined;
    sdkMocks.mutationOptions = undefined;
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
          <FormView resource="notes.Note" id="note-1" fields={fields} />
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

  test("keeps an existing-record form locked until its record loads", async () => {
    sdkMocks.record = null;

    function Harness(): ReactElement {
      const [loaded, setLoaded] = useState(false);
      return (
        <>
          <button
            type="button"
            onClick={() => {
              sdkMocks.record = {
                id: "note-1",
                title: "Loaded",
                status: "ACTIVE",
              };
              setLoaded(true);
            }}
          >
            load {String(loaded)}
          </button>
          <FormView resource="notes.Note" id="note-1" fields={fields} />
        </>
      );
    }

    renderWithProviders(<Harness />);

    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /load/ }));

    const title = await screen.findByRole("textbox", { name: "Title" });
    await waitFor(() =>
      expect((title as HTMLInputElement).value).toBe("Loaded"),
    );
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
        ...sdkMocks.record,
        ...data,
        id: context.id,
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
      vendor: { id: "vendor-1", displayName: "Vendor One" },
    };
    const submit = vi.fn(
      async (data: Record<string, unknown>, context: FormSubmitContext) => ({
        ...sdkMocks.record,
        ...data,
        id: context.id,
      }),
    );
    const relationFields = [
      { name: "displayName", label: "Display Name", title: true },
      {
        name: "vendor",
        label: "Vendor",
        widget: "many2one",
        omittable: true,
        options: [
          { value: "vendor-1", label: "Vendor One" },
          { value: "vendor-2", label: "Vendor Two" },
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
    // option label renders — proof the form holds "vendor-1", not the object.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Vendor/ }).textContent).toContain(
        "Vendor One",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /Vendor/ }));
    fireEvent.click(await screen.findByText("Vendor Two"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Vendor/ }).textContent).toContain(
        "Vendor Two",
      ),
    );
    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "Acme Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    // The current scalar identity wins over the saved expanded relation.
    expect(submit).toHaveBeenCalledWith(
      { displayName: "Acme Renamed", vendor: "vendor-2" },
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
    expect(sdkMocks.mutationAction).toBe("update");
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
    // The detail read folds the related record's label (`vendor.display_name`),
    // so the picker shows it with no option query. The list carries a DISTINCT
    // label, proving the read path uses the record's own label and never fetches
    // the 200-row option list until the picker is first opened.
    sdkMocks.record = {
      id: "provider-1",
      name: "Anthropic",
      vendor: { id: "vnd_1", display_name: "Anthropic Vendor" },
    };
    sdkMocks.listRows = [{ id: "vnd_1", display_name: "Vendor From List" }];
    const metadata: TestSchemaMetadata = {
      types: {
        InferenceProviderType: {
          ...defaultModel("InferenceProviderType", "agents.InferenceProvider"),
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
            vendor: {
              name: "vendor",
              kind: "relation",
              relationModelLabel: "Vendor",
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
        VendorType: {
          ...defaultModel("VendorType", "Vendor"),
          fields: {
            display_name: {
              name: "display_name",
              kind: "scalar",
              scalar: "String",
            },
          },
          resource: {
            ...defaultResource("VendorType", "Vendor"),
            recordRepresentation: "display_name",
            roots: { list: "vendors", detail: "vendor" },
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
          { name: "vendor", label: "Vendor" },
        ]}
      />,
      metadata,
    );

    // The trigger label comes from the record read, and the deferred option
    // list has NOT fired on the editable-form mount (the headline guarantee).
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Vendor: Anthropic Vendor" }),
      ).toBeTruthy(),
    );
    expect(sdkMocks.listEnabled).toBe(false);
    expect(sdkMocks.recordSelection).toContain("vendor");
    expect(sdkMocks.recordSelection).not.toContain("vendor.id");
    expect(sdkMocks.recordSelection).not.toContain("vendor.display_name");

    // Opening the picker fires the option list once; its fresh label then wins.
    fireEvent.click(
      screen.getByRole("button", { name: "Vendor: Anthropic Vendor" }),
    );
    await waitFor(() => expect(sdkMocks.listEnabled).toBe(true));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Vendor: Vendor From List" }),
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
    sdkMocks.mutate.mockReset();
    const metadata: TestSchemaMetadata = {
      types: {
        IntegrationType: {
          ...defaultModel("IntegrationType", "integrate.Integration"),
          fields: {
            displayName: { name: "displayName", kind: "scalar", scalar: "String" },
            vendor: { name: "vendor", kind: "relation", relationModelLabel: "Vendor" },
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
            createFields: ["vendor", "owner", "credential", "implClass", "config"],
          },
        },
      },
    };
    const integrationFields = [
      { name: "displayName", label: "Display Name", title: true },
      { name: "vendor", label: "Vendor" },
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
          vendor: "vendor-1",
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
        vendor: "vendor-1",
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
    sdkMocks.mutate.mockReset();
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
              ? { displayName: "Second preset", vendor: "vendor-2", privateConfig: "second-private" }
              : { displayName: "First preset", vendor: "vendor-1", privateConfig: "first-private" },
            prefillPreserveDirty: true,
            prefillReplace: ["privateConfig"],
          },
          { name: "vendor", label: "Vendor" },
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
      vendor: "vendor-2",
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
            prefill: () => ({ vendor: "vendor-2", privateConfig: "private" }),
            prefillPreserveDirty: true,
            prefillReplace: ["privateConfig"],
          },
          { name: "vendor", label: "Vendor" },
          { name: "privateConfig", label: "Private Config" },
        ]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Provider Type"), { target: { value: "second" } });
    expect((screen.getByLabelText("Vendor") as HTMLInputElement).value).toBe("vendor-2");
    expect((screen.getByLabelText("Private Config") as HTMLInputElement).value).toBe("private");

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect((screen.getByLabelText("Provider Type") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Vendor") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Private Config") as HTMLInputElement).value).toBe("");
  });

  test("binds a declarative required error to a dotted field", async () => {
    sdkMocks.mutate.mockReset();
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

    const retry = screen.getByText("Config Retry").closest('[data-layout="stack"]') as HTMLElement;
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

  test("provides the resource and record id to a record-chrome slot contribution", async () => {
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
      {
        slots: [
          {
            slot: FORM_VIEW_RECORD_CHROME_SLOT,
            id: "notes.chrome",
            content: <ChromeProbe />,
          },
        ],
      },
    );

    const probe = await screen.findByTestId("chrome-probe");
    expect(probe.textContent).toBe("notes.Note:note-1");
  });

  test("renders record-action slot contributions beside the Actions menu", async () => {
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
      {
        slots: [
          {
            ...formViewRecordActionsSlot("notes.Note"),
            id: "notes.pause",
            content: <ActionProbe />,
          },
        ],
      },
    );

    const actions = await screen.findByRole("button", { name: "Actions" });
    const pause = await screen.findByRole("button", {
      name: "Pause note-1 via console for notes.Note",
    });
    expect(pause.parentElement).toBe(actions.parentElement);
  });

  test("inherits record-action contributions from the model's canonical MTI parent", async () => {
    // The addon that owns a parent model contributes its verbs against it once
    // and reaches every subtype's form, instead of contributing globally and
    // re-deriving the MTI mapping in a predicate on every form in the app.
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      mtiMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "parties.pause",
            content: <button type="button">Inherited pause</button>,
          },
        ],
      },
    );

    expect(await screen.findByRole("button", { name: "Inherited pause" })).toBeTruthy();
  });

  test("lets an own-model contribution override the canonical entry of the same id", async () => {
    // A subtype specializing an inherited verb: decided by declared model
    // specificity, never by addon array order.
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      mtiMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.pause",
            content: <button type="button">Generic pause</button>,
          },
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.disconnect",
            content: <button type="button">Generic disconnect</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note"),
            id: "lifecycle.pause",
            content: <button type="button">Note pause</button>,
          },
        ],
      },
    );

    // Same id against the more specific model wins…
    expect(await screen.findByRole("button", { name: "Note pause" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Generic pause" })).toBeNull();
    // …and an inherited verb the subtype did not specialize is untouched.
    expect(screen.getByRole("button", { name: "Generic disconnect" })).toBeTruthy();
  });

  test("keeps the canonical parent's own form on its own entry", async () => {
    // The override is scoped to the subtype: the parent's form still renders the
    // parent's verb, so a subtype cannot reach up and replace it.
    renderWithProviders(
      <FormView resource="parties.Party" id="party-1">
        <Field name="title" label="Title" title />
      </FormView>,
      mtiMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.pause",
            content: <button type="button">Generic pause</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note"),
            id: "lifecycle.pause",
            content: <button type="button">Note pause</button>,
          },
        ],
      },
    );

    expect(await screen.findByRole("button", { name: "Generic pause" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Note pause" })).toBeNull();
  });

  test("lets an impl entry beat the own-model entry, which beats the canonical one", async () => {
    // The three tiers of the record-verb key, resolved by declared specificity:
    // canonical MTI parent → own model → own model + the row's impl key. This is
    // what lets one vendor's addon specialize a verb for its own rows without
    // naming — or displacing it on — a model it does not own.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.disconnect",
            content: <button type="button">Generic disconnect</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note"),
            id: "lifecycle.disconnect",
            content: <button type="button">Note disconnect</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "lifecycle.disconnect",
            content: <button type="button">WhatsApp disconnect</button>,
          },
        ],
      },
    );

    expect(
      await screen.findByRole("button", { name: "WhatsApp disconnect" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Note disconnect" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Generic disconnect" })).toBeNull();
  });

  test("selects the impl column it resolves the verb key from", async () => {
    // The regression this guards: the impl key is built from
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
      {
        slots: [
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "lifecycle.disconnect",
            content: <button type="button">WhatsApp disconnect</button>,
          },
        ],
      },
    );

    expect(
      await screen.findByRole("button", { name: "WhatsApp disconnect" }),
    ).toBeTruthy();
    expect(sdkMocks.recordSelection).toContain("kind");
  });

  test("selects no impl column the resource does not name", async () => {
    // The control for the test above. Same form, same row, same contribution —
    // only `implFields` is gone from the resource, so the view has no column to
    // select, the row arrives without `kind`, and the impl key never resolves.
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
      {
        slots: [
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "lifecycle.disconnect",
            content: <button type="button">WhatsApp disconnect</button>,
          },
        ],
      },
    );

    await screen.findByDisplayValue("Note");
    expect(sdkMocks.recordSelection).not.toContain("kind");
    expect(screen.queryByRole("button", { name: "WhatsApp disconnect" })).toBeNull();
  });

  test("keeps the inherited verbs on a row of another impl", async () => {
    // The IMAP case: WhatsApp's entries are keyed on its own impl, so a channel it
    // does not own is untouched by them and keeps integrate's canonical verbs.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "IMAP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.disconnect",
            content: <button type="button">Generic disconnect</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "lifecycle.disconnect",
            content: <button type="button">WhatsApp disconnect</button>,
          },
        ],
      },
    );

    expect(
      await screen.findByRole("button", { name: "Generic disconnect" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "WhatsApp disconnect" })).toBeNull();
  });

  test("resolves the impl key case-insensitively against the row's enum read", async () => {
    // The row reads its impl as the GraphQL enum member name (`WHATSAPP`) while an
    // addon spells the registry key (`whatsapp`); `formViewRecordActionsSlot` owns
    // that rule, so both sides land on one key.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "whatsapp.connect",
            content: <button type="button">Pair WhatsApp</button>,
          },
        ],
      },
    );

    expect(await screen.findByRole("button", { name: "Pair WhatsApp" })).toBeTruthy();
  });

  test("composes two vendors' entries on one model without a collision", async () => {
    // The cap the model-scoped key imposed: a second backend contributing the same
    // verb id for the same model hit the `uniqueKind` throw at boot. Distinct impl
    // keys make them siblings, and each row resolves only its own.
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "IMAP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "lifecycle.disconnect",
            content: <button type="button">WhatsApp disconnect</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note", "imap"),
            id: "lifecycle.disconnect",
            content: <button type="button">IMAP disconnect</button>,
          },
        ],
      },
    );

    expect(await screen.findByRole("button", { name: "IMAP disconnect" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "WhatsApp disconnect" })).toBeNull();
  });

  test("orders the merged record verbs by sequence across specificity tiers", async () => {
    // `sequence` stays the ordering contract once a subtype specializes: merging
    // two already-sorted groups by concatenation put the specialized verb's whole
    // group last, so a vendor's Connect(10) landed after the inherited Pause(11).
    sdkMocks.record = { ...sdkMocks.record, id: "note-1", kind: "WHATSAPP" };
    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      implMetadata(),
      undefined,
      {
        slots: [
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.pause",
            sequence: 11,
            content: <button type="button">Pause</button>,
          },
          {
            ...formViewRecordActionsSlot("parties.Party"),
            id: "lifecycle.disconnect",
            sequence: 13,
            content: <button type="button">Disconnect</button>,
          },
          {
            ...formViewRecordActionsSlot("notes.Note", "whatsapp"),
            id: "whatsapp.connect",
            sequence: 10,
            content: <button type="button">Connect</button>,
          },
        ],
      },
    );

    await screen.findByRole("button", { name: "Connect" });
    const rendered = screen
      .getAllByRole("button")
      .map((button) => button.textContent)
      .filter((label) => ["Connect", "Pause", "Disconnect"].includes(label ?? ""));
    expect(rendered).toEqual(["Connect", "Pause", "Disconnect"]);
  });

  test("merges FORM_VIEW_SECTIONS_SLOT fields into the submit payload", async () => {
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      {
        slots: [
          {
            ...formViewSectionsSlot("notes.Note"),
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

  test("composes FORM_VIEW_SECTIONS_SLOT tabs through the saved-record tab owner", async () => {
    sdkMocks.record = { id: "note-1", title: "Slot note" };

    function SlotRecordPane(): ReactElement {
      const context = useRecordChromeContext();
      return <p>Pane for {context.recordId}</p>;
    }

    renderWithProviders(
      <FormView resource="notes.Note" id="note-1">
        <Field name="title" label="Title" title />
      </FormView>,
      undefined,
      undefined,
      {
        slots: [
          {
            ...formViewSectionsSlot("notes.Note"),
            id: "notes.pane",
            content: (
              <Tab id="pane" label="Pane" badge={7}>
                <SlotRecordPane />
              </Tab>
            ),
          },
        ],
      },
    );

    const paneTab = await screen.findByRole("tab", { name: /Pane/ });
    expect(paneTab.textContent).toContain("7");
    fireEvent.click(paneTab);
    expect(await screen.findByText("Pane for note-1")).toBeTruthy();
  });

  test("rejects a slot record tab that claims the reserved overview id", async () => {
    sdkMocks.record = { id: "note-1", title: "Slot note" };
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() =>
        renderWithProviders(
          <FormView resource="notes.Note" id="note-1">
            <Field name="title" label="Title" title />
          </FormView>,
          undefined,
          undefined,
          {
            slots: [
              {
                ...formViewSectionsSlot("notes.Note"),
                id: "notes.overview",
                content: (
                  <Tab id="overview" label="Shadow overview">
                    <p>contributed</p>
                  </Tab>
                ),
              },
            ],
          },
        ),
      ).toThrow(/duplicate record tab id "overview"/);
    } finally {
      consoleError.mockRestore();
    }
  });

  test("rejects a slot record tab whose id collides with a declared one", async () => {
    sdkMocks.record = { id: "note-1", title: "Slot note" };
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
          {
            slots: [
              {
                ...formViewSectionsSlot("notes.Note"),
                id: "notes.pane",
                content: (
                  <Tab id="pane" label="Pane">
                    <p>contributed</p>
                  </Tab>
                ),
              },
            ],
          },
        ),
      ).toThrow(/duplicate record tab id "pane"/);
    } finally {
      consoleError.mockRestore();
    }
  });

  test("honors a custom relation widget in the overview and saves its selected id", async () => {
    sdkMocks.record = {
      id: "client-1", displayName: "Acme",
      vendor: { id: "vendor-1", displayName: "Vendor One" },
    };
    const custom = {
      read: () => <span>Custom vendor display</span>,
      edit: ({ value, onChange }: { value?: unknown; onChange?: (value: unknown) => void }) => (
        <input aria-label="Custom vendor" value={String(value ?? "")} onChange={(event) => onChange?.(event.target.value)} />
      ),
    };
    renderWithProviders(
      <FormView resource="OAuthClient" id="client-1" fields={[
        { name: "displayName", label: "Name", title: true },
        { name: "vendor", label: "Vendor", widget: "test.vendor" },
      ]} />,
      { types: {
        OAuthClientType: {
          ...defaultModel("OAuthClientType", "OAuthClient"),
          fields: {
            displayName: { name: "displayName", kind: "scalar", scalar: "String" },
            vendor: { name: "vendor", kind: "relation", relationModelLabel: "Widget", relationObject: true },
          },
        },
        WidgetType: {
          ...defaultModel("WidgetType", "Widget"),
          fields: { displayName: { name: "displayName", kind: "scalar", scalar: "String" } },
          resource: { ...defaultResource("WidgetType", "Widget"), recordRepresentation: "displayName" },
        },
      } }, undefined,
      { widgets: { ...defaultWidgets, "test.vendor": custom } },
    );
    const input = await screen.findByRole("textbox", { name: "Custom vendor" });
    expect(screen.queryByRole("button", { name: "Vendor" })).toBeNull();
    fireEvent.change(input, { target: { value: "vendor-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({ data: { id: "client-1", vendor: "vendor-2" } }));
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
        resource="OAuthClient"
        id="client-1"
        fields={relationFields}
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Vendor/ }).textContent).toContain(
        "Vendor One",
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
    sdkMocks.mutate.mockReset();
    renderWithProviders(
      <FormView
        resource="OAuthClient"
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
            resource="notes.Note"
            id="note-1"
            fields={viewFields}
            onSaved={(row) => {
              // This fixture mocks useOne outside Query; reflect the accepted
              // cache record before recreating the field projection.
              sdkMocks.record = row;
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
      const queryClient = useMemo(() => createTestQueryClient(), []);
      return (
        <QueryClientProvider client={queryClient}>
          <ModalsHost>
            <ToastProvider>
              <ModelMetadataProvider metadata={withDefaultResourceMetadata(undefined)}>
                <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
                  <Outlet />
                </AppRuntimeProvider>
              </ModelMetadataProvider>
            </ToastProvider>
          </ModalsHost>
        </QueryClientProvider>
      );
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
      const queryClient = useMemo(() => createTestQueryClient(), []);
      return (
        <QueryClientProvider client={queryClient}>
          <ModalsHost>
            <ToastProvider>
              <ModelMetadataProvider metadata={withDefaultResourceMetadata(undefined)}>
                <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
                  <Outlet />
                </AppRuntimeProvider>
              </ModelMetadataProvider>
            </ToastProvider>
          </ModalsHost>
        </QueryClientProvider>
      );
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
      const queryClient = useMemo(() => createTestQueryClient(), []);
      return (
        <QueryClientProvider client={queryClient}>
          <ModalsHost>
            <ToastProvider>
              <ModelMetadataProvider metadata={withDefaultResourceMetadata(undefined)}>
                <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
                  <Outlet />
                </AppRuntimeProvider>
              </ModelMetadataProvider>
            </ToastProvider>
          </ModalsHost>
        </QueryClientProvider>
      );
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

  test("workspace records open the validated default panel with Settings last", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        recordPresentation="workspace"
        defaultRecordTab="editor"
        overviewTab={{ label: "Settings", position: "last" }}
        recordExtras={() => <p>Related records</p>}
        recordTabs={[
          { id: "editor", label: "Editor", keepMounted: true, render: () => <button type="button">Editor action</button> },
          { id: "runs", label: "Runs", render: () => <p>Runs panel</p> },
        ]}
      />,
    );

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Editor", "Runs", "Settings"]);
    expect(screen.getByRole("button", { name: "Editor action" })).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.queryByText("ACTIVE")).toBeNull();
    expect(screen.queryByLabelText("Reminder")).toBeNull();
    expect(screen.queryByText("Related records")).toBeNull();
    expect(document.querySelector("form")?.className).toContain("contents");

    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(await screen.findByLabelText("Reminder")).toBeTruthy();
    expect(screen.getByText("Related records")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Editor action" })).toBeNull();
  });

  test("document records honor overview tab placement without changing presentation", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
        id="note-1"
        fields={fields}
        defaultRecordTab="activity"
        overviewTab={{ label: "Settings", position: "last" }}
        recordTabs={[
          { id: "activity", label: "Activity", render: () => <p>Activity panel</p> },
        ]}
      />,
    );

    expect((await screen.findAllByRole("tab")).map((tab) => tab.textContent)).toEqual([
      "Activity",
      "Settings",
    ]);
    expect(screen.getByText("Activity panel")).toBeTruthy();
    expect(document.querySelector("form")?.className).toContain("min-h-full");
  });

  test("workspace tab switches preserve dirty settings and reset to the default for a new record", async () => {
    function Harness(): ReactElement {
      const [id, setId] = useState("note-1");
      return <>
        <button type="button" onClick={() => setId("note-2")}>Next note</button>
        <FormView resource="notes.Note" id={id} fields={fields}
          recordPresentation="workspace" defaultRecordTab="editor"
          overviewTab={{ label: "Settings", position: "last" }}
          recordTabs={[{ id: "editor", label: "Editor", render: () => <p>Editor panel</p> }]} />
      </>;
    }
    renderWithProviders(<Harness />);

    expect(await screen.findByText("Editor panel")).toBeTruthy();
    const title = await screen.findByLabelText("Title");
    fireEvent.change(title, { target: { value: "Dirty title" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    expect(await screen.findByLabelText("Reminder")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
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
        overviewTab={{ label: "Settings", position: "last" }}
        recordTabs={[{ id: "editor", label: "Editor", render: () => <p>Editor panel</p> }]} />,
    );
    expect(await screen.findByLabelText("Title")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Settings" }).getAttribute("aria-selected")).toBe("true");

    cleanup();
    sdkMocks.record = null;
    renderWithProviders(
      <FormView resource="notes.Note" id={null} fields={fields}
        recordPresentation="workspace" defaultRecordTab="editor"
        overviewTab={{ label: "Settings", position: "last" }}
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
            vendor: {
              name: "vendor",
              kind: "relation",
              relationModelLabel: "Vendor",
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
    expect(selection).toContain("vendor"); // scalar-id relation → bare leaf
    expect(selection).not.toContain("vendor.id");
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
    sdkMocks.mutate.mockReset();
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
    sdkMocks.mutate.mockReset();
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
    sdkMocks.mutate.mockReset();
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
    sdkMocks.mutate.mockReset();
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
    sdkMocks.mutate.mockReset();
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
    sdkMocks.mutate.mockReset();
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
    // Only field errors → the banner names declared labels and raw server-only keys.
    expect(
      screen.getByText("Please fix the highlighted fields: Title, environment."),
    ).toBeTruthy();
  });

  // Editable document lines (F6): the resource metadata carries a `linesResource`
  // and a `save` root, so FormView renders the lines composer and routes a dirty
  // save through `<resource>_save(pk, patch, lines)`.
  test("new document renders Add line and submits lines with its first create", async () => {
    sdkMocks.record = null;
    sdkMocks.mutate.mockResolvedValue({ id: "doc-new", title: "Quotation", lines: [{ id: "line-new", label: "Lamp", position: 0 }] });
    renderSaleDoc(null);
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Quotation" } });
    fireEvent.click(screen.getByRole("button", { name: "Add line" }));
    fireEvent.change(screen.getByLabelText("Text", { exact: true }), { target: { value: "Lamp" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Quotation", lines: { data: [{ label: "Lamp", position: 0 }] } },
    }));
    expect(sdkMocks.save).not.toHaveBeenCalled();
  });

  test("seeds document lines without a reseed loop", async () => {
    sdkMocks.record = saleDocRecord();
    renderSaleDoc();

    // Both seeded rows render; a reseed loop would exhaust React's update depth
    // (the fix is the memo-stabilized seed array threaded through the reset).
    expect(await screen.findByDisplayValue("Keep")).toBeTruthy();
    expect(screen.getByDisplayValue("Drop")).toBeTruthy();
    await act(async () => {
      await nextTask();
    });
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Order");
    expect(screen.getByDisplayValue("Keep")).toBeTruthy();
    expect(sdkMocks.save).not.toHaveBeenCalled();
  });

  test("routes a dirty-lines save through the resource save mutation", async () => {
    sdkMocks.record = saleDocRecord();
    sdkMocks.save.mockImplementation(
      async (variables: {
        pk: string;
        patch?: Record<string, unknown>;
        lines?: readonly Record<string, unknown>[];
      }) => ({
        id: "doc-1",
        title: "Order",
        lines: (variables.lines ?? []).map((line, index) => ({
          ...line,
          id: line.id ?? `new-${index}`,
        })),
      }),
    );
    renderSaleDoc();

    fireEvent.change(await screen.findByDisplayValue("Keep"), {
      target: { value: "Kept" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.save).toHaveBeenCalledTimes(1));
    // Parent-only patch is empty (title untouched); the full desired line list
    // carries each existing row's id and the row-order position.
    expect(sdkMocks.save).toHaveBeenCalledWith({
      pk: "doc-1",
      patch: {},
      lines: [
        { id: "ln-1", label: "Kept", quantity: 1, position: 0 },
        { id: "ln-2", label: "Drop", quantity: 9, position: 1 },
      ],
    });
    // The stock update path is never taken when lines are dirty.
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  // The slice-7 acceptance step: a new line whose numeric cells are left blank
  // must save — the untouched Int/Decimal cells are omitted from the line input
  // (Strawberry rejects "" for those scalars) so the model defaults apply.
  test("a new line with untouched numeric cells creates without those keys", async () => {
    sdkMocks.record = saleDocRecord();
    sdkMocks.save.mockImplementation(
      async (variables: { lines?: readonly Record<string, unknown>[] }) => ({
        id: "doc-1",
        title: "Order",
        lines: (variables.lines ?? []).map((line, index) => ({
          ...line,
          id: line.id ?? `new-${index}`,
        })),
      }),
    );
    renderSaleDoc();

    await screen.findByDisplayValue("Keep");
    fireEvent.click(screen.getByRole("button", { name: "Add line" }));
    // Fill only the label; quantity (Int) and price (Decimal) stay untouched.
    const newLabelCell = screen
      .getAllByLabelText("Text")
      .find((cell) => (cell as HTMLInputElement).value === "");
    expect(newLabelCell).toBeTruthy();
    fireEvent.change(newLabelCell as HTMLInputElement, {
      target: { value: "New" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.save).toHaveBeenCalledTimes(1));
    const variables = sdkMocks.save.mock.calls[0]?.[0] as {
      lines: readonly Record<string, unknown>[];
    };
    // The created row carries only the typed cell and its position — the blank
    // numeric cells are absent so the save document coerces and defaults apply.
    expect(variables.lines[2]).toEqual({ label: "New", position: 2 });
    expect(variables.lines[2]).not.toHaveProperty("quantity");
    expect(variables.lines[2]).not.toHaveProperty("price");
    expect(variables.lines[0]).toEqual(
      expect.objectContaining({ id: "ln-1", quantity: 1 }),
    );
  });

  test("keeps a parent-only edit on the stock update path", async () => {
    sdkMocks.record = saleDocRecord();
    renderSaleDoc();

    await screen.findByDisplayValue("Keep");
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledTimes(1));
    expect(sdkMocks.mutate).toHaveBeenCalledWith({
      data: { title: "Renamed", id: "doc-1" },
    });
    // No line changed, so the diff-apply save root is never invoked.
    expect(sdkMocks.save).not.toHaveBeenCalled();
  });

  test("maps a line save validation error to its row", async () => {
    sdkMocks.record = saleDocRecord();
    sdkMocks.save.mockRejectedValue({
      graphQLErrors: [
        {
          message: "Validation failed.",
          extensions: {
            code: "VALIDATION",
            validationErrors: { "lines.1.label": ["This field is required."] },
            formErrors: [],
          },
        },
      ],
    });
    renderSaleDoc();

    fireEvent.change(await screen.findByDisplayValue("Keep"), {
      target: { value: "Kept" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sdkMocks.save).toHaveBeenCalledTimes(1));
    // The row-1 cell surfaces its server message from the projected rowErrors.
    expect(await screen.findByText("This field is required.")).toBeTruthy();
  });
});

function saleDocRecord(): Row {
  return {
    id: "doc-1",
    title: "Order",
    lines: [
      { id: "ln-1", label: "Keep", quantity: 1, position: 0 },
      { id: "ln-2", label: "Drop", quantity: 9, position: 1 },
    ],
  };
}

function renderSaleDoc(id: string | null = "doc-1"): void {
  renderWithProviders(
    <FormView
      resource="demo.SaleDoc"
      id={id}
      fields={[{ name: "title", label: "Title", title: true }]}
    />,
    SALES_METADATA,
    undefined,
    undefined,
    SALES_DOCUMENTS,
  );
}

function saleLineField(
  name: string,
  scalar: string,
  extra: Partial<DataResourceFieldMetadata> = {},
): DataResourceFieldMetadata {
  return {
    name,
    kind: "scalar",
    scalar,
    readable: true,


    aggregatable: false,

    creatable: true,
    updatable: true,
    requiredOnCreate: false,
    ...extra,
  };
}

const SALES_METADATA: TestSchemaMetadata = {
  types: {
    SaleDocType: {
      ...defaultModel("SaleDocType", "demo.SaleDoc"),
      fields: { title: { name: "title", kind: "scalar", scalar: "String" } },
      resource: {
        schemaName: "console",
        modelLabel: "demo.SaleDoc",
        appLabel: "demo",
        modelName: "SaleDoc",
        query: testResourceQuery(),
        recordRepresentation: "title",
        roots: {
          list: "sale_docs",
          create: "insert_sale_docs_one",
          detail: "sale_docs_by_pk",
          update: "update_sale_docs_by_pk",
          save: "sale_docs_save",
        },
        typeNames: { node: "SaleDocType", createInput: "sale_docs_insert_input", updateInput: "sale_docs_set_input" },
        capabilities: ["list", "detail", "create", "update", "save"],
        fields: [saleLineField("title", "String", { requiredOnCreate: true })],


        aggregateFields: [],


        linesResource: {
          field: "lines",
          modelLabel: "demo.SaleLine",
          inputType: "sale_docs_lines_insert_input",
          positionField: "position",
          fields: [
            saleLineField("label", "String", { requiredOnCreate: true }),
            saleLineField("quantity", "Int"),
            saleLineField("price", "Decimal"),
            saleLineField("position", "Int"),
          ],
        },
      },
    },
  },
};

const SALES_DOCUMENTS = {
  console: { saves: { "demo.SaleDoc": { kind: "Document", definitions: [] } } },
};

function renderForm(id: string | null): void {
  renderWithProviders(<FormView resource="notes.Note" id={id} fields={fields} />);
}

function renderWithProviders(
  children: ReactElement,
  metadata?: TestSchemaMetadata,
  forms?: FormOverrideMap,
  runtime?: Partial<AppRuntime>,
  documents?: ComponentProps<typeof OperationDocumentsProvider>["documents"],
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
  const queryClient = createTestQueryClient();

  render(
    <QueryClientProvider client={queryClient}>
      <RouterContextProvider router={router}>
        <ModalsHost>
          <ToastProvider>
            <OperationDocumentsProvider documents={documents ?? {}}>
              <ModelMetadataProvider metadata={withDefaultResourceMetadata(metadata)}>
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
            </OperationDocumentsProvider>
          </ToastProvider>
        </ModalsHost>
      </RouterContextProvider>
    </QueryClientProvider>,
  );
}

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
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
  const modelName = modelNameForLabel(modelLabel);
  const list = `${modelName.toLowerCase()}s`;
  return {
    schemaName: "console",
    modelLabel,
    appLabel: modelLabel.includes(".") ? modelLabel.split(".")[0] ?? "" : "",
    modelName,
    query: testResourceQuery(),
    roots: {
      list,
      detail: `${list}_by_pk`,
      create: `insert_${list}_one`,
      update: `update_${list}_by_pk`,
      delete: `delete_${list}_by_pk`,
    },
    typeNames: { node: typeName },
    capabilities: ["list", "detail", "create", "update", "delete"],
    fields: [],


    aggregateFields: [],


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
function implMetadata(): SchemaFieldMetadata {
  const note = mtiModel("NoteType", "notes.Note", "parties.Party", ["kind"]);
  return withTestResourceInventory({
    types: {
      NoteType: {
        ...note,
        fields: { ...note.fields, kind: { name: "kind", kind: "scalar", scalar: "String" } },
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

function modelNameForLabel(modelLabel: string): string {
  return modelLabel.split(".").at(-1) ?? modelLabel;
}

function cloneFields(source: readonly FormField[]): FormField[] {
  return source.map((field) => ({ ...field }));
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
