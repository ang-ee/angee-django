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
import { ModalsHost, baseIcons } from "@angee/base";

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
  revokeRole: vi.fn(),
  revokeState: {
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
      return {
        data: undefined,
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useAuthoredMutation: () => [sdkMocks.revokeRole, sdkMocks.revokeState],
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
    sdkMocks.revokeRole.mockReset();
    sdkMocks.revokeState.fetching = false;
    sdkMocks.revokeState.error = null;
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
      <ModalsHost>
        <Outlet />
      </ModalsHost>
    </AppRuntimeProvider>
  );
}

function TestScreen(): ReactElement | null {
  const context = useContext(TestUrlStateContext);
  return context ? <>{context.children}</> : null;
}
