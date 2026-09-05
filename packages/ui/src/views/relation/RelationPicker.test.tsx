// @vitest-environment happy-dom

import type {
  SchemaFieldMetadata,
} from "@angee/metadata";
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
  } from "../../runtime";
import {
  ModelMetadataProvider,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type {
  Row,
} from "@angee/metadata";
import { useState, type ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { defaultWidgets } from "../../widgets";
import { RelationPicker } from "./RelationPicker";

const sdkMocks = vi.hoisted(() => ({
  record: null as Row | null,
  mutate: vi.fn(),
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return actual;
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
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
    // FormView unconditionally mounts the F6 `useAngeeResourceSave` hook (over
    // `useCustomMutation`) and `useInvalidate`; this relation form declares no
    // lines, so neither ever fires — the no-ops keep them off the QueryClient.
    useCustomMutation: () => ({
      mutateAsync: async () => ({ data: {} }),
      mutation: { isPending: false, error: null, reset: vi.fn() },
    }),
    useInvalidate: () => vi.fn(async () => undefined),
  };
});



const options = [
  { value: "client-1", label: "Acme OAuth" },
  { value: "client-2", label: "Globex OAuth" },
];

const editConfig = {
  resource: "integrate.OAuthClient",
  fields: [{ name: "displayName", label: "Display Name", title: true }],
};

const oauthResource = testDataResource("integrate.OAuthClient", {
  modelName: "OAuthClient",
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
});

const metadata: SchemaFieldMetadata = {
  resources: [oauthResource],
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
      resource: oauthResource,
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

  test("passes field accessibility ids to the relation trigger", () => {
    renderPicker(
      <RelationPicker
        id="decision-target"
        value="client-1"
        options={options}
        aria-label="OAuth Client"
        aria-describedby="decision-target-error"
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "OAuth Client: Acme OAuth",
    });
    expect(trigger.id).toBe("decision-target");
    expect(trigger.getAttribute("aria-describedby")).toBe(
      "decision-target-error",
    );
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

  test("localizes the default inline create dialog title", async () => {
    renderPicker(
      <RelationPicker
        options={[]}
        create={{
          resource: "integrate.OAuthClient",
          fields: [{ name: "displayName", label: "Display Name", title: true }],
        }}
        aria-label="OAuth Client"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "OAuth Client" }));
    fireEvent.change(await screen.findByPlaceholderText("Search…"), {
      target: { value: "Acme" },
    });
    fireEvent.click(await screen.findByText("Create “Acme”"));

    expect(await screen.findByText("New oauthclient")).toBeTruthy();
  });
});

function QueryOwner({ children }: { children: ReactElement }): ReactElement {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  }));
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

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
    <QueryOwner>
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
    </QueryOwner>
  );
}

function renderPicker(children: ReactElement): ReturnType<typeof render> {
  return render(wrap(children));
}
