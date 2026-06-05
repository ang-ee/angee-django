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
import { AppRuntimeProvider } from "@angee/sdk";
import { ModalsHost, ToastProvider, baseIcons } from "@angee/base";

import { ConnectionsPage } from "./ConnectionsPage";
import { GrantsPage } from "./GrantsPage";
import { UsersPage } from "./UsersPage";

const sdkMocks = vi.hoisted(() => ({
  users: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  grants: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  oauthClients: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  externalAccounts: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  vendors: {
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
  revokeRole: vi.fn(),
  createOauthClient: vi.fn(),
  updateOauthClient: vi.fn(),
  createExternalAccount: vi.fn(),
  revokeState: {
    fetching: false,
    error: null as Error | null,
  },
  createState: {
    fetching: false,
    error: null as Error | null,
  },
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredQuery: (document: string) => {
      if (document.includes("IamUsers")) return sdkMocks.users;
      if (document.includes("IamGrants")) return sdkMocks.grants;
      if (document.includes("IamOAuthClients")) return sdkMocks.oauthClients;
      if (document.includes("IamExternalAccounts")) return sdkMocks.externalAccounts;
      if (document.includes("IamVendorOptions")) return sdkMocks.vendors;
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
      if (document.includes("IamCreateOAuthClient")) {
        return [sdkMocks.createOauthClient, sdkMocks.createState];
      }
      if (document.includes("IamUpdateOAuthClient")) {
        return [sdkMocks.updateOauthClient, sdkMocks.createState];
      }
      if (document.includes("IamCreateExternalAccount")) {
        return [sdkMocks.createExternalAccount, sdkMocks.createState];
      }
      return [sdkMocks.revokeRole, sdkMocks.revokeState];
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
    sdkMocks.users.data = undefined;
    sdkMocks.users.fetching = false;
    sdkMocks.users.error = null;
    sdkMocks.users.refetch.mockReset();
    sdkMocks.grants.data = undefined;
    sdkMocks.grants.fetching = false;
    sdkMocks.grants.error = null;
    sdkMocks.grants.refetch.mockReset();
    sdkMocks.oauthClients.data = undefined;
    sdkMocks.oauthClients.fetching = false;
    sdkMocks.oauthClients.error = null;
    sdkMocks.oauthClients.refetch.mockReset();
    sdkMocks.externalAccounts.data = undefined;
    sdkMocks.externalAccounts.fetching = false;
    sdkMocks.externalAccounts.error = null;
    sdkMocks.externalAccounts.refetch.mockReset();
    sdkMocks.vendors.data = undefined;
    sdkMocks.vendors.fetching = false;
    sdkMocks.vendors.error = null;
    sdkMocks.vendors.refetch.mockReset();
    sdkMocks.connectionSummary.data = undefined;
    sdkMocks.connectionSummary.fetching = false;
    sdkMocks.connectionSummary.error = null;
    sdkMocks.connectionSummary.refetch.mockReset();
    sdkMocks.revokeRole.mockReset();
    sdkMocks.createOauthClient.mockReset();
    sdkMocks.updateOauthClient.mockReset();
    sdkMocks.createExternalAccount.mockReset();
    sdkMocks.revokeState.fetching = false;
    sdkMocks.revokeState.error = null;
    sdkMocks.createState.fetching = false;
    sdkMocks.createState.error = null;
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

  test("renders loading, empty, and error list branches", async () => {
    sdkMocks.users.fetching = true;
    const { unmount } = renderInRouter(<UsersPage />);
    expect(await screen.findByText("Loading...")).toBeTruthy();
    unmount();

    sdkMocks.users.fetching = false;
    sdkMocks.users.data = {
      users: {
        totalCount: 0,
        results: [],
      },
    };
    renderInRouter(<UsersPage />);
    expect(await screen.findByText("No records.")).toBeTruthy();
    cleanup();

    sdkMocks.users.data = undefined;
    sdkMocks.users.error = new Error("Users unavailable");
    renderInRouter(<UsersPage />);
    expect(await screen.findByText("Users unavailable")).toBeTruthy();
  });

  test("creates an OIDC provider from the connections page", async () => {
    seedConnectionData();
    sdkMocks.createOauthClient.mockResolvedValue({ createOauthClient: {} });

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
      expect(sdkMocks.createOauthClient).toHaveBeenCalledWith(
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
    sdkMocks.oauthClients.data = {
      oauthClients: {
        totalCount: 1,
        results: [
          oauthClientFixture({
            linkOnEmailMatch: true,
            createOnLogin: true,
          }),
        ],
      },
    };
    sdkMocks.updateOauthClient.mockResolvedValue({ updateOauthClient: {} });

    renderInRouter(<ConnectionsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Open Acme prod" }),
    );
    await screen.findByText("Edit OIDC provider");
    expect(
      screen.getByRole("combobox", { name: "User resolution" }).textContent,
    ).toContain("Create users and link by email");
    expect(
      (screen.getByLabelText("Client secret") as HTMLInputElement).value,
    ).toBe("stored-secret");
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Acme staging" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));

    await waitFor(() =>
      expect(sdkMocks.updateOauthClient).toHaveBeenCalledWith(
        expect.objectContaining({
          data: expect.objectContaining({
            id: "client-1",
            vendor: "vendor-1",
            displayName: "Acme staging",
            clientId: "acme-client",
            clientSecret: "stored-secret",
            linkOnEmailMatch: true,
            createOnLogin: true,
          }),
        }),
      ),
    );
  });

  test("edits an external account from the connections page", async () => {
    seedConnectionData();
    const account = externalAccountFixture();
    sdkMocks.externalAccounts.data = {
      externalAccounts: {
        totalCount: 1,
        results: [account],
      },
    };
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
  sdkMocks.vendors.data = {
    vendors: {
      totalCount: 1,
      results: [vendor],
    },
  };
  sdkMocks.oauthClients.data = {
    oauthClients: {
      totalCount: 0,
      results: [],
    },
  };
  sdkMocks.externalAccounts.data = {
    externalAccounts: {
      totalCount: 0,
      results: [],
    },
  };
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
  sdkMocks.users.data = {
    users: {
      totalCount: 0,
      results: [],
    },
  };
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
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
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
