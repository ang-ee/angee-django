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

import { OverviewPage } from "./OverviewPage";

const ALICE_RELAY_ID = "VXNlclR5cGU6dXNyXzE=";
const STALE_RELAY_ID = "VXNlclR5cGU6dXNyXzI=";

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
  grantRole: vi.fn(),
  grantState: {
    fetching: false,
    error: null as Error | null,
  },
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredQuery: (document: string) => {
      if (document.includes("IamOverview")) return sdkMocks.overview;
      if (document.includes("IamUsers")) return sdkMocks.users;
      return {
        data: undefined,
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useAuthoredMutation: () => [sdkMocks.grantRole, sdkMocks.grantState],
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
    sdkMocks.grantRole.mockReset();
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
    sdkMocks.users.data = usersData({ totalCount: 0, results: [] });
    renderInRouter(<OverviewPage />);

    expect((await selectTrigger("Principal")).hasAttribute("disabled")).toBe(
      true,
    );
  });

  test("shows when the principal picker is capped", async () => {
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.data = usersData({
      totalCount: 501,
      results: [userData({ id: ALICE_RELAY_ID })],
    });

    renderInRouter(<OverviewPage />);

    expect(
      await screen.findByText("Showing first 500 of 501 users."),
    ).toBeTruthy();
  });

  test("submits the selected user's relay id and renders the user label on success", async () => {
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.data = usersData({
      totalCount: 1,
      results: [userData({ id: ALICE_RELAY_ID })],
    });
    sdkMocks.grantRole.mockResolvedValue({ grantRole: true });

    renderInRouter(<OverviewPage />);

    await chooseSelect("Principal", "alice <alice@example.com>");
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Role" }).textContent).toContain(
        "angee / Writer",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(sdkMocks.grantRole).toHaveBeenCalledWith({
        principalId: ALICE_RELAY_ID,
        role: "angee/role:writer",
      }),
    );
    expect(await screen.findByText("Role granted")).toBeTruthy();
    expect(
      screen.getAllByText("alice <alice@example.com>").length,
    ).toBeGreaterThan(0);
    expect(sdkMocks.overview.refetch).toHaveBeenCalledTimes(1);
  });

  test("falls back to the selected principal id when the user row is stale", async () => {
    const staleUser = userData({
      id: STALE_RELAY_ID,
      username: "stale",
      email: "",
    });
    sdkMocks.overview.data = overviewData();
    sdkMocks.users.data = usersData({
      totalCount: 1,
      results: [staleUser],
    });
    sdkMocks.grantRole.mockResolvedValue({ grantRole: true });

    renderInRouter(<OverviewPage />);

    await chooseSelect("Principal", "stale");
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Role" }).textContent).toContain(
        "angee / Writer",
      ),
    );
    staleUser.id = "VXNlclR5cGU6dXNyXzk=";
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(sdkMocks.grantRole).toHaveBeenCalledWith({
        principalId: STALE_RELAY_ID,
        role: "angee/role:writer",
      }),
    );
    expect(await screen.findByText("Role granted")).toBeTruthy();
    expect(screen.getByText(STALE_RELAY_ID)).toBeTruthy();
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

function overviewData(): unknown {
  return {
    users: {
      totalCount: 1,
    },
    roles: [
      {
        id: "writer",
        namespace: "angee",
        label: "Writer",
        description: "",
      },
    ],
    grants: {
      totalCount: 0,
    },
    relationships: {
      totalCount: 0,
    },
  };
}

function usersData({
  totalCount,
  results,
}: {
  totalCount: number;
  results: unknown[];
}): unknown {
  return {
    users: {
      totalCount,
      results,
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
  firstName: string;
  lastName: string;
  email: string;
  isStaff: boolean;
  isActive: boolean;
} {
  return {
    id,
    username,
    firstName: "",
    lastName: "",
    email,
    isStaff: false,
    isActive: true,
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
