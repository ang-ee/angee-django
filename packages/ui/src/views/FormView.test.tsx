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
  } from "../runtime";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import { OperationDocumentsProvider } from "@angee/refine";
import type {
  Row,
} from "@angee/metadata";
import { useMemo, useState, type ComponentProps, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { defaultWidgets } from "../widgets";
import { Form } from "./Form";
import {
  FormView,
  FORM_VIEW_RECORD_CHROME_SLOT,
  formViewSectionsSlot,
  type FormField,
  type FormSubmitContext,
} from "./FormView";
import { useRecordChromeContext } from "./record-chrome-context";
import {
  Action,
  Field,
  Group,
} from "./page";

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
  mutationAction: undefined as string | undefined,
  mutationOptions: undefined as { fields?: readonly string[]; enabled?: boolean } | undefined,
}));

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
  const formResult = (options?: {
    action?: "create" | "edit";
    id?: string | number;
    meta?: unknown;
    queryOptions?: { enabled?: boolean };
  }) => {
    const action = options?.action === "edit" ? "update" : "create";
    sdkMocks.recordSelection = fieldsFromMeta(options?.meta);
    sdkMocks.mutationAction = action;
    sdkMocks.mutationOptions = {
      fields: fieldsFromMeta(options?.meta),
      enabled: true,
    };
    const queryEnabled = options?.queryOptions?.enabled !== false;
    return {
      id: options?.id,
      setId: vi.fn(),
      query: {
        data: { data: queryEnabled ? sdkMocks.record ?? undefined : undefined },
        isFetching: false,
        error: null,
        refetch: vi.fn(),
      },
      mutation: { isPending: false, error: null, status: "idle" },
      formLoading: false,
      onFinish: async (values: Record<string, unknown>) => ({
        data: await sdkMocks.mutate({
          data: action === "update"
            ? ({ ...values, id: options?.id } as Row)
            : (values as Row),
        }),
      }),
      redirect: vi.fn(),
      overtime: {},
      autoSaveProps: { status: "idle", data: undefined, error: null },
      onFinishAutoSave: vi.fn(),
    };
  };
  return {
    ...actual,
    // The lines save path invalidates the resource caches after a custom-mutation
    // write (no Refine provider in this harness); a no-op keeps every form render safe.
    useInvalidate: () => vi.fn(async () => undefined),
    useForm: formResult,
    useOne: (options?: { meta?: unknown }) => {
      sdkMocks.recordSelection = fieldsFromMeta(options?.meta);
      return {
        result: sdkMocks.record ?? undefined,
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

vi.mock("@refinedev/react-hook-form", async () => {
  const hookForm = await import("react-hook-form");
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
  return {
    useForm: (options: {
      defaultValues?: Record<string, unknown>;
      refineCoreProps?: {
        action?: "create" | "edit";
        id?: string | number;
        meta?: unknown;
        queryOptions?: { enabled?: boolean };
      };
    } = {}) => {
      const form = hookForm.useForm({ defaultValues: options.defaultValues });
      const refineCore = (() => {
        const action =
          options.refineCoreProps?.action === "edit" ? "update" : "create";
        sdkMocks.recordSelection = fieldsFromMeta(options.refineCoreProps?.meta);
        sdkMocks.mutationAction = action;
        sdkMocks.mutationOptions = {
          fields: fieldsFromMeta(options.refineCoreProps?.meta),
          enabled: true,
        };
        const queryEnabled =
          options.refineCoreProps?.queryOptions?.enabled !== false;
        return {
          id: options.refineCoreProps?.id,
          setId: vi.fn(),
          query: {
            data: {
              data: queryEnabled ? sdkMocks.record ?? undefined : undefined,
            },
            isFetching: false,
            error: null,
            refetch: vi.fn(),
          },
          mutation: { isPending: false, error: null, status: "idle" },
          formLoading: false,
          onFinish: async (values: Record<string, unknown>) => ({
            data: await sdkMocks.mutate({
              data: action === "update"
                ? ({ ...values, id: options.refineCoreProps?.id } as Row)
                : (values as Row),
            }),
          }),
          redirect: vi.fn(),
          overtime: {},
          autoSaveProps: { status: "idle", data: undefined, error: null },
          onFinishAutoSave: vi.fn(),
        };
      })();
      return {
        ...form,
        refineCore,
        saveButtonProps: {
          disabled: false,
          onClick: (event: unknown) => {
            void form.handleSubmit((values) => refineCore.onFinish(values))(
              event as never,
            );
          },
        },
      };
    },
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
    sdkMocks.save.mockReset();
    sdkMocks.listRows = [];
    sdkMocks.listEnabled = false;
    sdkMocks.recordSelection = undefined;
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

  test("renders record-aware toolbarStart content", async () => {
    renderWithProviders(
      <FormView
        resource="notes.Note"
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
        resource="notes.Note"
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
    const metadata: SchemaFieldMetadata = {
      types: {
        InferenceProviderType: {
          typeName: "InferenceProviderType",
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
          },
          rootFields: {
            list: "inference_providers",
            detail: "inference_providers_by_pk",
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
    fireEvent.change(screen.getByLabelText("Display Name"), {
      target: { value: "Acme Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    // The custom owner receives FormView's normalized payload: the unchanged
    // relation is dropped, never forwarded as a nested record to re-flatten.
    expect(submit).toHaveBeenCalledWith(
      { displayName: "Acme Renamed" },
      expect.objectContaining({ id: "client-1", isCreate: false }),
    );
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("does not offer save without a stock update root or custom submit", async () => {
    sdkMocks.record = { id: "provider-1", name: "Anthropic" };
    const resource = {
      ...defaultResource("InferenceProviderType", "agents.InferenceProvider"),
      roots: {
        ...defaultResource("InferenceProviderType", "agents.InferenceProvider").roots,
        update: null,
      },
      capabilities: ["list", "detail", "delete"],
    } satisfies DataResourceMetadata;
    const metadata: SchemaFieldMetadata = {
      types: {
        InferenceProviderType: {
          typeName: "InferenceProviderType",
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
          },
          rootFields: {
            list: "inference_providers",
            detail: "inference_providers_by_pk",
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

    await waitFor(() =>
      expect(
        (screen.getByLabelText("Name") as HTMLInputElement).value,
      ).toBe("Anthropic"),
    );
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  test("does not require a mutation for a read-only detail form", async () => {
    sdkMocks.record = { id: "repo-1", name: "widgets", org: "acme" };
    renderWithProviders(
      <FormView
        resource="integrate.Repository"
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
    const metadata: SchemaFieldMetadata = {
      types: {
        InferenceProviderType: {
          typeName: "InferenceProviderType",
          fields: {
            name: { name: "name", kind: "scalar", scalar: "String" },
            vendor: {
              name: "vendor",
              kind: "relation",
              relationTarget: "VendorType",
            },
          },
          rootFields: {
            list: "inference_providers",
            detail: "inference_provider",
            update: "update_inference_provider",
          },
          resource: defaultResource("InferenceProviderType", "agents.InferenceProvider"),
        },
        VendorType: {
          typeName: "VendorType",
          recordRepresentation: "display_name",
          fields: {
            display_name: {
              name: "display_name",
              kind: "scalar",
              scalar: "String",
            },
          },
          rootFields: {
            list: "vendors",
            detail: "vendor",
          },
          resource: defaultResource("VendorType", "Vendor"),
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
    expect(sdkMocks.recordSelection).toContain("vendor.id");
    expect(sdkMocks.recordSelection).toContain("vendor.display_name");

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
      data: { title: "", status: "ACTIVE", reminderAt: null },
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
    const metadata: SchemaFieldMetadata = {
      types: {
        IntegrationType: {
          typeName: "IntegrationType",
          fields: {
            displayName: { name: "displayName", kind: "scalar", scalar: "String" },
            vendor: { name: "vendor", kind: "relation", relationTarget: "VendorType" },
            owner: { name: "owner", kind: "relation", relationTarget: "UserType" },
            credential: {
              name: "credential",
              kind: "relation",
              relationTarget: "CredentialType",
            },
            implClass: { name: "implClass", kind: "scalar", scalar: "String" },
            implLabel: { name: "implLabel", kind: "scalar", scalar: "String" },
            config: { name: "config", kind: "scalar", scalar: "JSON" },
            lastError: { name: "lastError", kind: "scalar", scalar: "String" },
          },
          rootFields: {
            create: "createIntegration",
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
      <FormView resource="notes.Note" fields={[{ name: "code", label: "Code" }]} />,
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
          extensions: {
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
          extensions: {
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

function renderSaleDoc(): void {
  renderWithProviders(
    <FormView
      resource="demo.SaleDoc"
      id="doc-1"
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
    filterable: false,
    sortable: false,
    aggregatable: false,
    groupable: false,
    creatable: true,
    updatable: true,
    requiredOnCreate: false,
    ...extra,
  };
}

const SALES_METADATA: SchemaFieldMetadata = {
  types: {
    SaleDocType: {
      typeName: "SaleDocType",
      recordRepresentation: "title",
      fields: { title: { name: "title", kind: "scalar", scalar: "String" } },
      rootFields: {
        list: "sale_docs",
        detail: "sale_docs_by_pk",
        update: "update_sale_docs_by_pk",
      },
      resource: {
        schemaName: "console",
        modelLabel: "demo.SaleDoc",
        appLabel: "demo",
        modelName: "SaleDoc",
        publicIdField: "id",
        roots: {
          list: "sale_docs",
          detail: "sale_docs_by_pk",
          update: "update_sale_docs_by_pk",
          save: "sale_docs_save",
        },
        typeNames: { node: "SaleDocType", updateInput: "sale_docs_set_input" },
        capabilities: ["list", "detail", "update", "save"],
        fields: [saleLineField("title", "String", { requiredOnCreate: true })],
        filterFields: [],
        orderFields: [],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
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
  metadata?: SchemaFieldMetadata,
  forms?: Record<string, unknown>,
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
  metadata: SchemaFieldMetadata | undefined,
): SchemaFieldMetadata {
  const seed = metadata ?? { types: {} };
  const types: Record<string, ModelMetadata> = {
    NoteType: defaultModel("NoteType", "notes.Note"),
    RepositoryType: defaultModel("RepositoryType", "integrate.Repository"),
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
  return { ...seed, types };
}

function defaultModel(typeName: string, modelLabel: string): ModelMetadata {
  const modelName = modelNameForLabel(modelLabel);
  return {
    typeName,
    fields: {},
    rootFields: {
      list: `${modelName.toLowerCase()}s`,
      detail: `${modelName.toLowerCase()}_by_pk`,
      create: `create${modelName}`,
      update: `update${modelName}`,
    },
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
    publicIdField: "id",
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
    filterFields: [],
    orderFields: [],
    aggregateFields: [],
    groupByFields: [],
    relationAxes: [],
  };
}

function modelLabelForType(typeName: string): string {
  const known: Record<string, string> = {
    NoteType: "notes.Note",
    RepositoryType: "integrate.Repository",
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

function statusStep(label: string): Element | null {
  return screen.getByText(label).closest("[role='listitem']");
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
