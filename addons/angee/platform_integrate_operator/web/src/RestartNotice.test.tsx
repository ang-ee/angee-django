// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { RestartNotice } from "./RestartNotice";

const mocks = vi.hoisted(() => ({
  connection: null as { restartJob?: string | null } | null,
  operation: null as Record<string, unknown> | null,
  refetch: vi.fn(),
  run: vi.fn(),
  scope: "settings" as "apps" | "settings",
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: () => ({
    data: { platform_explorer: { pending_addon_changes: false } },
    error: null,
    isFetching: false,
    refetch: mocks.refetch,
  }),
}));

vi.mock("@angee/platform", () => ({
  PendingAddonChanges: {},
  PLATFORM_ADDON_MUTATION_INVALIDATES: ["platform.Addon"],
}));

vi.mock("@angee/operator/runtime", () => ({
  useOperatorConnection: () => mocks.connection,
  useJobRunOperation: () => ({
    operation: mocks.operation,
    active: false,
    starting: false,
    startError: null,
    queryError: null,
    run: mocks.run,
  }),
}));

vi.mock("@angee/ui", () => ({
  Banner: ({ title, children }: { title: string; children?: ReactNode }) => <div><strong>{title}</strong>{children}</div>,
  Button: ({ children }: { children?: ReactNode }) => <button>{children}</button>,
  NavLink: ({ children }: { children?: ReactNode }) => <a>{children}</a>,
  useRouteHref: () => () => "/operator/operations",
}));

vi.mock("@angee/ui/chrome/refine-menu", () => ({
  useChromePlace: () => ({ railPlace: { scope: mocks.scope } }),
}));

vi.mock("./i18n", () => ({
  usePlatformIntegrateOperatorT: () => (key: string) => key,
}));

afterEach(cleanup);

beforeEach(() => {
  mocks.connection = null;
  mocks.operation = null;
  mocks.refetch.mockClear();
  mocks.run.mockClear();
  mocks.scope = "settings";
});

describe("RestartNotice", () => {
  test("shows only where the console's place is Settings, as the route's anchor decides", () => {
    mocks.connection = { restartJob: "restart-application" };
    mocks.operation = { id: "run-1", rootJob: "restart-application", chainedRestart: true, status: "RUNNING", currentStep: "Restarting web", nodes: [] };
    mocks.scope = "apps";
    const { container } = render(<RestartNotice />);
    expect(container.firstChild).toBeNull();
  });

  test("renders nothing when connection and receipt are both absent", () => {
    const { container } = render(<RestartNotice />);

    expect(container.firstChild).toBeNull();
  });

  test("renders a running configured application restart receipt", () => {
    mocks.connection = { restartJob: "restart-application" };
    mocks.operation = {
      id: "run-1",
      rootJob: "restart-application",
      chainedRestart: true,
      status: "RUNNING",
      currentStep: "Restarting web",
      nodes: [],
    };

    render(<RestartNotice />);

    expect(screen.getByText("restart.running.title")).not.toBeNull();
    expect(screen.getByText("Restarting web")).not.toBeNull();
  });
});
