// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  grantRole: vi.fn(),
  overview: { data: undefined as unknown, isFetching: false, error: null },
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => mocks.overview,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useAuthoredResourceMutation: () => [mocks.grantRole, { fetching: false, error: null }],
}));

vi.mock("../SubjectControl", () => ({
  SubjectControl: ({ onChange }: { onChange: (value: string) => void }) => (
    <button type="button" onClick={() => onChange("auth/group:7#member")}>Choose group</button>
  ),
}));

import { AppRuntimeProvider, ModalsHost, baseIcons } from "@angee/ui";

import { OverviewPage } from "./OverviewPage";

describe("IAM overview page", () => {
  afterEach(() => {
    cleanup();
    mocks.grantRole.mockReset();
    mocks.overview.data = undefined;
  });

  test("offers declared zero-member roles and grants a canonical group subject", async () => {
    mocks.overview.data = overviewData();
    mocks.grantRole.mockResolvedValue({ grant_role: true });
    renderPage(<OverviewPage />);

    fireEvent.click(screen.getAllByRole("button", { name: "Grant" }).at(-1)!);
    fireEvent.click(await screen.findByRole("button", { name: "Choose group" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Grant" }).at(-1)!);

    await waitFor(() => expect(mocks.grantRole).toHaveBeenCalledWith({
      subject: "auth/group:7#member",
      role: "angee/role:reader",
    }));
  });

  test("offers only writable roles, excluding derived and legacy rows", async () => {
    mocks.overview.data = overviewData();
    renderPage(<OverviewPage />);

    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    expect(await screen.findByRole("option", { name: "angee / Reader" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "angee / Admin" })).toBeNull();
    expect(screen.queryByRole("option", { name: "angee / Removed" })).toBeNull();
  });
});

function overviewData(): unknown {
  return {
    iam_roles: [
      { id: "angee/role:reader", role_id: "reader", namespace: "angee", label: "Reader", declared: true, grantable: true },
      { id: "angee/role:admin", role_id: "admin", namespace: "angee", label: "Admin", declared: true, grantable: false },
      { id: "angee/role:removed", role_id: "removed", namespace: "angee", label: "Removed", declared: false, grantable: false },
    ],
    iam_overview: {
      user_count: 0,
      role_count: 3,
      grant_count: 0,
      relationship_count: 0,
      privileged_grant_count: 0,
      unassigned_user_count: 0,
      namespaces: [],
      privileged_grants: [],
      unassigned_users: [],
    },
  };
}

function renderPage(children: ReactNode): ReturnType<typeof render> {
  return render(
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <ModalsHost>{children}</ModalsHost>
    </AppRuntimeProvider>,
  );
}
