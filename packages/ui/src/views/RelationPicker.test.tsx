// @vitest-environment happy-dom

import type {
  SchemaFieldMetadata,
} from "@angee/resources";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  } from "@testing-library/react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  } from "@tanstack/react-router";
import {
  AppRuntimeProvider,
  } from "../runtime";
import {
  ModelMetadataProvider,
} from "@angee/resources";
import type {
  Row,
} from "@angee/resources";
import { type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { defaultWidgets } from "../widgets";
import { RelationPicker } from "./RelationPicker";

const sdkMocks = vi.hoisted(() => ({
  record: null as Row | null,
  mutate: vi.fn(),
}));

vi.mock("../data/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/hooks")>();
  return actual;
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useForm: (options?: {
      action?: "create" | "edit";
      id?: string | number;
      queryOptions?: { enabled?: boolean };
    }) => {
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
            data: options?.action === "edit"
              ? ({ ...values, id: options?.id } as Row)
              : (values as Row),
          }),
        }),
        redirect: vi.fn(),
        overtime: {},
        autoSaveProps: { status: "idle", data: undefined, error: null },
        onFinishAutoSave: vi.fn(),
      };
    },
    useOne: () => ({
      result: sdkMocks.record ?? undefined,
      query: { isFetching: false, error: null, refetch: vi.fn() },
    }),
    useCreate: () => ({
      mutateAsync: async ({ values = {} }: { values?: Record<string, unknown> }) => ({
        data: await sdkMocks.mutate({ data: values }),
      }),
      mutation: { isPending: false, error: null },
    }),
    useUpdate: () => ({
      mutateAsync: async ({
        id,
        values = {},
      }: {
        id?: string | number;
        values?: Record<string, unknown>;
      }) => ({
        data: await sdkMocks.mutate({ data: { ...values, id } }),
      }),
      mutation: { isPending: false, error: null },
    }),
  };
});

vi.mock("@refinedev/react-hook-form", async () => {
  const hookForm = await import("react-hook-form");
  return {
    useForm: (options: {
      defaultValues?: Record<string, unknown>;
      refineCoreProps?: {
        action?: "create" | "edit";
        id?: string | number;
        queryOptions?: { enabled?: boolean };
      };
    } = {}) => {
      const form = hookForm.useForm({ defaultValues: options.defaultValues });
      const queryEnabled =
        options.refineCoreProps?.queryOptions?.enabled !== false;
      const refineCore = {
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
            data: options.refineCoreProps?.action === "edit"
              ? ({ ...values, id: options.refineCoreProps?.id } as Row)
              : (values as Row),
          }),
        }),
        redirect: vi.fn(),
        overtime: {},
        autoSaveProps: { status: "idle", data: undefined, error: null },
        onFinishAutoSave: vi.fn(),
      };
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

const options = [
  { value: "client-1", label: "Acme OAuth" },
  { value: "client-2", label: "Globex OAuth" },
];

const editConfig = {
  resource: "OAuthClient",
  fields: [{ name: "displayName", label: "Display Name", title: true }],
};

const metadata: SchemaFieldMetadata = {
  types: {
    OAuthClientType: {
      typeName: "OAuthClientType",
      recordRepresentation: "displayName",
      fields: {
        id: { name: "id", kind: "scalar", scalar: "ID" },
        displayName: {
          name: "displayName",
          kind: "scalar",
          scalar: "String",
          label: "Display Name",
        },
      },
      rootFields: {
        list: "oauth_clients",
        detail: "oauth_clients_by_pk",
        create: "insert_oauth_clients_one",
        update: "update_oauth_clients_by_pk",
      },
      resource: {
        schemaName: "console",
        modelLabel: "OAuthClient",
        appLabel: "",
        modelName: "OAuthClient",
        publicIdField: "id",
        roots: {
          list: "oauth_clients",
          detail: "oauth_clients_by_pk",
          create: "insert_oauth_clients_one",
          update: "update_oauth_clients_by_pk",
          delete: "delete_oauth_clients_by_pk",
        },
        typeNames: { node: "OAuthClientType" },
        capabilities: ["list", "detail", "create", "update", "delete"],
        fields: [],
        filterFields: [],
        orderFields: [],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

describe("RelationPicker edit affordance", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    sdkMocks.record = { id: "client-1", displayName: "Acme OAuth" };
    sdkMocks.mutate.mockReset();
  });

  test("shows the edit pencil only when a record is selected", () => {
    const { rerender } = renderPicker(
      <RelationPicker
        value={null}
        options={options}
        edit={editConfig}
        aria-label="OAuth Client"
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();

    rerender(
      wrap(
        <RelationPicker
          value="client-1"
          options={options}
          edit={editConfig}
          aria-label="OAuth Client"
        />,
      ),
    );
    expect(screen.getByRole("button", { name: "Edit record" })).toBeTruthy();
  });

  test("hides the edit pencil when read-only", () => {
    renderPicker(
      <RelationPicker
        value="client-1"
        options={options}
        edit={editConfig}
        readOnly
        aria-label="OAuth Client"
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();
  });

  test("opens the selected record in an edit dialog", async () => {
    renderPicker(
      <RelationPicker
        value="client-1"
        options={options}
        edit={editConfig}
        aria-label="OAuth Client"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit record" }));

    // The dialog opens on the selected record (its title field is seeded).
    await screen.findByText("Edit oauthclient");
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Display Name") as HTMLInputElement).value,
      ).toBe("Acme OAuth"),
    );
  });
});

function wrap(children: ReactElement): ReactElement {
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
  return (
    <RouterContextProvider router={router}>
      <ModalsHost>
        <ToastProvider>
          <ModelMetadataProvider metadata={metadata}>
            <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
              {children}
            </AppRuntimeProvider>
          </ModelMetadataProvider>
        </ToastProvider>
      </ModalsHost>
    </RouterContextProvider>
  );
}

function renderPicker(children: ReactElement): ReturnType<typeof render> {
  return render(wrap(children));
}
