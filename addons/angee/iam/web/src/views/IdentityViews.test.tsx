// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import {
  createContext,
  useContext,
  useMemo,
  type ReactElement,
  type ReactNode,
} from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, type Row } from "@angee/sdk";
import { ModalsHost, ToastProvider, baseIcons, defaultWidgets } from "@angee/base";

import { ConnectionsPage } from "./ConnectionsPage";
import { GrantsPage } from "./GrantsPage";
import { UsersPage } from "./UsersPage";

const sdkMocks = vi.hoisted(() => ({
  grants: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  connectionSummary: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  lists: {
    User: listState(),
    Vendor: listState(),
    OAuthClient: listState(),
    ExternalAccount: listState(),
  },
  records: {} as Record<string, Row | null>,
  revokeRole: vi.fn(),
  mutateVendor: vi.fn(),
  mutateOauthClient: vi.fn(),
  createExternalAccount: vi.fn(),
  revokeState: {
    fetching: false,
    error: null as Error | null,
  },
  mutationState: {
    fetching: false,
    error: null as Error | null,
  },
  resourceListCalls: [] as string[],
}));

function listState() {
  return {
    rows: [] as readonly Row[],
    total: 0 as number | undefined,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  };
}

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredQuery: (document: string) => {
      if (document.includes("IamGrants")) return sdkMocks.grants;
      if (document.includes("IamConnectionSummary")) {
        return sdkMocks.connectionSummary;
      }
      return {
        data: undefined,
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useAuthoredMutation: (document: string) => {
      return [sdkMocks.revokeRole, sdkMocks.revokeState];
    },
    useResourceList: (model: string) => {
      sdkMocks.resourceListCalls.push(model);
      const state = sdkMocks.lists[model as keyof typeof sdkMocks.lists] ?? listState();
      return {
        ...state,
        page: 1,
        pageSize: 50,
        pageCount: state.total === undefined ? undefined : 1,
        pageInfo: undefined,
        hasNext: false,
        hasPrev: false,
        setPage: vi.fn(),
        firstPage: vi.fn(),
        nextPage: vi.fn(),
        prevPage: vi.fn(),
        lastPage: vi.fn(),
      };
    },
    useResourceRecord: (model: string, id: string | null) => ({
      record: id ? sdkMocks.records[`${model}:${id}`] ?? null : null,
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
    useResourceMutation: (model: string) => {
      if (model === "Vendor") return [sdkMocks.mutateVendor, sdkMocks.mutationState];
      if (model === "OAuthClient") {
        return [sdkMocks.mutateOauthClient, sdkMocks.mutationState];
      }
      if (model === "ExternalAccount") {
        return [sdkMocks.createExternalAccount, sdkMocks.mutationState];
      }
      return [vi.fn(), sdkMocks.mutationState];
    },
  };
});

describe("IAM identity views", () => {
  beforeAll(() => {
    Object.defineProperty(Element.prototype, "getAnimations", {
      configurable: true,
      value: () => [],
    });
  });

  afterEach(() => {
    cleanup();
    sdkMocks.grants.data = undefined;
    sdkMocks.grants.fetching = false;
    sdkMocks.grants.error = null;
    sdkMocks.grants.refetch.mockReset();
    sdkMocks.connectionSummary.data = undefined;
    sdkMocks.connectionSummary.fetching = false;
    sdkMocks.connectionSummary.error = null;
    sdkMocks.connectionSummary.refetch.mockReset();
    for (const list of Object.values(sdkMocks.lists)) {
      list.rows = [];
      list.total = 0;
      list.fetching = false;
      list.error = null;
      list.refetch.mockReset();
    }
    sdkMocks.records = {};
    sdkMocks.resourceListCalls = [];
    sdkMocks.revokeRole.mockReset();
    sdkMocks.mutateVendor.mockReset();
    sdkMocks.mutateOauthClient.mockReset();
    sdkMocks.createExternalAccount.mockReset();
    sdkMocks.revokeState.fetching = false;
    sdkMocks.revokeState.error = null;
    sdkMocks.mutationState.fetching = false;
    sdkMocks.mutationState.error = null;
  });

  test("revokes a grant through the confirm dialog and refetches", async () => {
    sdkMocks.grants.data = grantsData();
    sdkMocks.revokeRole.mockResolvedValue({ revokeRole: true });

    renderInRouter(<GrantsPage />);

    await screen.findByRole("button", { name: "Revoke" });
    await nextTask();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await screen.findByText("Revoke role?");
    fireEvent.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1)!);

    await waitFor(() =>
      expect(sdkMocks.revokeRole).toHaveBeenCalledWith({
        principalId: "user-1",
        role: "iam/admin",
      }),
    );
    expect(sdkMocks.grants.refetch).toHaveBeenCalledTimes(1);
  });

  test("surfaces revoke errors", async () => {
    sdkMocks.grants.data = grantsData();
    sdkMocks.revokeRole.mockRejectedValue(new Error("Permission denied"));

    renderInRouter(<GrantsPage />);

    await screen.findByRole("button", { name: "Revoke" });
    await nextTask();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await screen.findByText("Revoke role?");
    fireEvent.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1)!);

    expect(await screen.findByText("Role was not revoked")).toBeTruthy();
    expect(screen.getByText("Permission denied")).toBeTruthy();
    expect(sdkMocks.grants.refetch).not.toHaveBeenCalled();
  });

  test("reads users through the resource list hook", async () => {
    sdkMocks.lists.User.rows = [];
    sdkMocks.lists.User.total = 0;

    renderInRouter(<UsersPage />);

    await screen.findByText("No records.");
    expect(sdkMocks.resourceListCalls).toContain("User");
  });

  test("renders loading, empty, and error list branches", async () => {
    sdkMocks.lists.User.fetching = true;
    const { unmount } = renderInRouter(<UsersPage />);
    expect(await screen.findByText("Loading...")).toBeTruthy();
    unmount();

    sdkMocks.lists.User.fetching = false;
    sdkMocks.lists.User.rows = [];
    sdkMocks.lists.User.total = 0;
    renderInRouter(<UsersPage />);
    expect(await screen.findByText("No records.")).toBeTruthy();
    cleanup();

    sdkMocks.lists.User.error = new Error("Users unavailable");
    renderInRouter(<UsersPage />);
    expect(await screen.findByText("Users unavailable")).toBeTruthy();
  });

  test("creates an OIDC provider from the connections page", async () => {
    seedConnectionData();
    sdkMocks.mutateOauthClient.mockResolvedValue({ id: "client-1" });

    renderInRouter(<ConnectionsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "New OIDC provider" }),
    );
    const displayName = await screen.findByLabelText("Display name");
    fireEvent.change(displayName, {
      target: { value: "Acme prod" },
    });
    fireEvent.change(screen.getByLabelText("Client ID"), {
      target: { value: "acme-client" },
    });
    fireEvent.change(screen.getByLabelText("Client secret"), {
      target: { value: "acme-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create provider" }));

    await waitFor(() =>
      expect(sdkMocks.mutateOauthClient).toHaveBeenCalledWith(
        expect.objectContaining({
          data: expect.objectContaining({
            vendor: "vendor-1",
            displayName: "Acme prod",
            clientId: "acme-client",
            clientSecret: "acme-secret",
            environment: "prod",
            isOidc: true,
            isEnabled: true,
          }),
        }),
      ),
    );
  });

  test("creates an external account from the connections page", async () => {
    seedConnectionData();
    sdkMocks.createExternalAccount.mockResolvedValue({
      createExternalAccount: {},
    });

    renderInRouter(<ConnectionsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "New external account" }),
    );
    await screen.findByText("External account");
    fireEvent.change(screen.getByLabelText("External ID"), {
      target: { value: "acct-123" },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "acct@example.com" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save external account" }),
    );

    await waitFor(() =>
      expect(sdkMocks.createExternalAccount).toHaveBeenCalledWith({
        data: expect.objectContaining({
          vendor: "vendor-1",
          externalId: "acct-123",
          email: "acct@example.com",
          status: "active",
        }),
      }),
    );
  });

  test("edits an OIDC provider from the connections page", async () => {
    seedConnectionData();
    const client = oauthClientFixture({
      linkOnEmailMatch: true,
      createOnLogin: true,
    }) as Row;
    sdkMocks.lists.OAuthClient.rows = [client];
    sdkMocks.lists.OAuthClient.total = 1;
    sdkMocks.records["OAuthClient:client-1"] = client;
    sdkMocks.mutateOauthClient.mockResolvedValue({ id: "client-1" });

    renderInRouter(<ConnectionsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Open Acme prod" }),
    );
    await screen.findByText("Edit OIDC provider");
    expect(
      (screen.getByLabelText("Client secret") as HTMLInputElement).value,
    ).toBe("stored-secret");
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Acme staging" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));

    await waitFor(() =>
      expect(sdkMocks.mutateOauthClient).toHaveBeenCalledWith(
        expect.objectContaining({
          data: expect.objectContaining({
            id: "client-1",
            displayName: "Acme staging",
          }),
        }),
      ),
    );
  });

  test("edits an external account from the connections page", async () => {
    seedConnectionData();
    const account = externalAccountFixture();
    sdkMocks.lists.ExternalAccount.rows = [account as Row];
    sdkMocks.lists.ExternalAccount.total = 1;
    sdkMocks.connectionSummary.data = {
      vendors: {
        totalCount: 1,
        results: [
          {
            id: "vendor-1",
            slug: "acme",
            displayName: "Acme",
            websiteUrl: "",
            icon: "",
            description: "",
          },
        ],
      },
      externalAccounts: {
        totalCount: 1,
        results: [account],
      },
      credentialHealth: {
        totalCount: 0,
        results: [],
      },
    };
    sdkMocks.createExternalAccount.mockResolvedValue({
      createExternalAccount: {},
    });

    renderInRouter(<ConnectionsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Open Ops identity" }),
    );
    await screen.findByText("External account");
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "ops-updated@example.com" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save external account" }),
    );

    await waitFor(() =>
      expect(sdkMocks.createExternalAccount).toHaveBeenCalledWith({
        data: expect.objectContaining({
          vendor: "vendor-1",
          externalId: "ops-sub",
          email: "ops-updated@example.com",
          displayName: "Ops identity",
          status: "active",
        }),
      }),
    );
  });
});

function grantsData(): unknown {
  return {
    grants: {
      totalCount: 1,
      results: [
        {
          principalId: "user-1",
          principalType: "user",
          role: "iam/admin",
        },
      ],
    },
  };
}

function seedConnectionData(): void {
  const vendor = {
    id: "vendor-1",
    slug: "acme",
    displayName: "Acme",
    websiteUrl: "",
    icon: "",
    description: "",
  };
  sdkMocks.lists.Vendor.rows = [vendor as Row];
  sdkMocks.lists.Vendor.total = 1;
  sdkMocks.lists.OAuthClient.rows = [];
  sdkMocks.lists.OAuthClient.total = 0;
  sdkMocks.lists.ExternalAccount.rows = [];
  sdkMocks.lists.ExternalAccount.total = 0;
  sdkMocks.connectionSummary.data = {
    vendors: {
      totalCount: 1,
      results: [vendor],
    },
    externalAccounts: {
      totalCount: 0,
      results: [],
    },
    credentialHealth: {
      totalCount: 0,
      results: [],
    },
  };
  sdkMocks.lists.User.rows = [];
  sdkMocks.lists.User.total = 0;
}

function oauthClientFixture(overrides: Record<string, unknown> = {}): unknown {
  const vendor = {
    id: "vendor-1",
    slug: "acme",
    displayName: "Acme",
    websiteUrl: "",
    icon: "",
    description: "",
  };
  return {
    id: "client-1",
    displayName: "Acme prod",
    vendor,
    vendorLabel: "Acme",
    vendorSlug: "acme",
    environment: "prod",
    clientId: "acme-client",
    clientSecret: "stored-secret",
    issuer: "",
    authorizeEndpoint: "",
    tokenEndpoint: "",
    revokeEndpoint: "",
    userinfoEndpoint: "",
    jwksUri: "",
    discoveryUrl: "",
    isOidc: true,
    isEnabled: true,
    configurationState: "ready",
    supportsRefresh: true,
    refreshRotates: false,
    supportsPkce: true,
    maxRefreshAgeSeconds: null,
    linkOnEmailMatch: false,
    createOnLogin: false,
    scopesCatalogue: ["openid", "email", "profile"],
    defaultScopes: ["openid", "email"],
    allowedEmailDomains: [],
    ...overrides,
  };
}

function externalAccountFixture(): unknown {
  return {
    id: "account-1",
    externalId: "ops-sub",
    email: "ops@example.com",
    displayName: "Ops identity",
    avatarUrl: "",
    status: "active",
    credentialStatus: "",
    lastUsedAt: null,
    vendor: {
      id: "vendor-1",
      slug: "acme",
      displayName: "Acme",
    },
  };
}

function renderInRouter(children: ReactNode): ReturnType<typeof render> {
  return render(<TestUrlState>{children}</TestUrlState>);
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });
}

const TestUrlStateContext = createContext<{ children: ReactNode } | null>(null);

function TestUrlState({ children }: { children: ReactNode }): ReactElement {
  const router = useMemo(() => {
    const rootRoute = createRootRoute({ component: TestRootRoute });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: TestScreen,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
      defaultPreload: false,
    });
  }, []);

  return (
    <TestUrlStateContext.Provider value={{ children }}>
      <RouterProvider router={router} />
    </TestUrlStateContext.Provider>
  );
}

function TestRootRoute(): ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
      <ToastProvider>
        <ModalsHost>
          <Outlet />
        </ModalsHost>
      </ToastProvider>
    </AppRuntimeProvider>
  );
}

function TestScreen(): ReactElement | null {
  const context = useContext(TestUrlStateContext);
  return context ? <>{context.children}</> : null;
}
