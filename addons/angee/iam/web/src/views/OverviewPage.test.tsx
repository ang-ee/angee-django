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
import { AppRuntimeProvider, ModalsHost, baseIcons } from "@angee/ui";

import { OverviewPage } from "./OverviewPage";
import { documentName } from "./test-documents";

const ALICE_PUBLIC_ID = "usr_1";
const STALE_PUBLIC_ID = "usr_2";

const sdkMocks = vi.hoisted(() => ({
  overview: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  users: {
    data: undefined as unknown,
    fetching: false,
    error: null as Error | null,
    refetch: vi.fn(),
  },
  grant_role: vi.fn(),
  grantState: {
    fetching: false,
    error: null as Error | null,
  },
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useAuthoredQuery: (document: unknown) => {
      const name = documentName(document);
      if (name === "IamOverview") return sdkMocks.overview;
      if (name === "IamUsers") return sdkMocks.users;
      return {
        data: undefined,
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useAuthoredMutation: () => [sdkMocks.grant_role, sdkMocks.grantState],
  };
});

describe("IAM overview page", () => {
  beforeAll(() => {
    Object.defineProperty(Element.prototype, "getAnimations", {
      configurable: true,
      value: () => [],
    });
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: class ResizeObserver {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
      },
    });
  });

  afterEach(() => {
    cleanup();
    sdkMocks.overview.data = undefined;
    sdkMocks.overview.fetching = false;
    sdkMocks.overview.error = null;
    sdkMocks.overview.refetch.mockReset();
    sdkMocks.users.data = undefined;
    sdkMocks.users.fetching = false;
    sdkMocks.users.error = null;
    sdkMocks.users.refetch.mockReset();
    sdkMocks.grant_role.mockReset();
    sdkMocks.grantState.fetching = false;
    sdkMocks.grantState.error = null;
  });

  test("disables the principal picker while loading and when no users are available", async () => {
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.fetching = true;

    const loading = renderInRouter(<OverviewPage />);
    expect((await selectTrigger("Principal")).hasAttribute("disabled")).toBe(
      true,
    );
    loading.unmount();

    sdkMocks.users.fetching = false;
    sdkMocks.users.data = usersData({ total_count: 0, users: [] });
    renderInRouter(<OverviewPage />);

    expect((await selectTrigger("Principal")).hasAttribute("disabled")).toBe(
      true,
    );
  });

  test("shows when the principal picker is capped", async () => {
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.data = usersData({
      total_count: 501,
      users: [userData({ id: ALICE_PUBLIC_ID })],
    });

    renderInRouter(<OverviewPage />);

    expect(
      await screen.findByText("Showing first 500 of 501 users."),
    ).toBeTruthy();
  });

  test("renders backend overview aggregates independently of the picker page", async () => {
    sdkMocks.overview.data = overviewData({
      user_count: 506,
      role_count: 2,
      grant_count: 3,
      relationship_count: 3,
      privileged_grant_count: 2,
      unassigned_user_count: 503,
      namespaces: [{ namespace: "angee", role_count: 2, grant_count: 3 }],
      privileged_grants: [
        {
          principal_id: "1",
          principal_type: "auth/user",
          principal_label: "Admin User",
          principal_ref: "auth/user:1",
          role: "angee/role:admin",
          role_name: "admin",
          namespace: "angee",
        },
      ],
      unassigned_users: [
        userData({
          id: STALE_PUBLIC_ID,
          username: "unassigned",
          email: "unassigned@example.com",
        }),
      ],
    });
    sdkMocks.users.data = usersData({
      total_count: 1,
      users: [userData({ id: ALICE_PUBLIC_ID })],
    });

    renderInRouter(<OverviewPage />);

    expect(await screen.findByText("503 without direct roles")).toBeTruthy();
    expect(screen.getByText("Admin User")).toBeTruthy();
    expect(screen.getByText("2 roles")).toBeTruthy();
    expect(screen.getByText("3 grants")).toBeTruthy();
    expect(screen.getByText("unassigned")).toBeTruthy();
  });

  test("submits the selected user's public id and renders the user label on success", async () => {
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.data = usersData({
      total_count: 1,
      users: [userData({ id: ALICE_PUBLIC_ID })],
    });
    sdkMocks.grant_role.mockResolvedValue({ grant_role: true });

    renderInRouter(<OverviewPage />);

    await chooseSelect("Principal", "alice <alice@example.com>");
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Role" }).textContent).toContain(
        "angee / Writer",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(sdkMocks.grant_role).toHaveBeenCalledWith({
        principal_id: ALICE_PUBLIC_ID,
        role: "angee/role:writer",
      }),
    );
    // The grant refetches the inventory so the new binding appears in the panels.
    await waitFor(() =>
      expect(sdkMocks.overview.refetch).toHaveBeenCalledTimes(1),
    );
  });

  test("falls back to the selected principal id when the user row is stale", async () => {
    const staleUser = userData({
      id: STALE_PUBLIC_ID,
      username: "stale",
      email: "",
    });
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.data = usersData({
      total_count: 1,
      users: [staleUser],
    });
    sdkMocks.grant_role.mockResolvedValue({ grant_role: true });

    renderInRouter(<OverviewPage />);

    await chooseSelect("Principal", "stale");
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Role" }).textContent).toContain(
        "angee / Writer",
      ),
    );
    staleUser.id = "usr_9";
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(sdkMocks.grant_role).toHaveBeenCalledWith({
        principal_id: STALE_PUBLIC_ID,
        role: "angee/role:writer",
      }),
    );
  });
});

async function chooseSelect(label: string, option: string): Promise<void> {
  fireEvent.click(await selectTrigger(label));
  fireEvent.click(await screen.findByRole("option", { name: option }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: label }).textContent).toContain(
      option,
    ),
  );
}

async function selectTrigger(label: string): Promise<HTMLElement> {
  return screen.findByRole("combobox", { name: label });
}

function overviewData(overrides: Record<string, unknown> = {}): unknown {
  return {
    roles: [
      {
        id: "writer",
        namespace: "angee",
        label: "Writer",
        description: "",
      },
    ],
    iam_overview: {
      user_count: 1,
      role_count: 1,
      grant_count: 0,
      relationship_count: 0,
      privileged_grant_count: 0,
      unassigned_user_count: 1,
      namespaces: [{ namespace: "angee", role_count: 1, grant_count: 0 }],
      privileged_grants: [],
      unassigned_users: [],
      ...overrides,
    },
  };
}

function usersData({
  total_count,
  users,
}: {
  total_count: number;
  users: unknown[];
}): unknown {
  return {
    users,
    users_aggregate: {
      aggregate: {
        count: total_count,
      },
    },
  };
}

function userData({
  id,
  username = "alice",
  email = "alice@example.com",
}: {
  id: string;
  username?: string;
  email?: string;
}): {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  is_staff: boolean;
  is_active: boolean;
} {
  return {
    id,
    username,
    first_name: "",
    last_name: "",
    email,
    is_staff: false,
    is_active: true,
  };
}

function renderInRouter(children: ReactNode): ReturnType<typeof render> {
  return render(<TestUrlState>{children}</TestUrlState>);
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
