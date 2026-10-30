// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import iam from "../index";

const mocks = vi.hoisted(() => ({
  grantRole: vi.fn(),
  navigate: vi.fn(),
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

import { AppRuntimeProvider, InAppLinkProvider, ModalsHost, ToastProvider, baseIcons, createRouteHref, defaultWidgets } from "@angee/ui";

import { OverviewPage } from "./OverviewPage";

describe("IAM overview page", () => {
  afterEach(() => {
    cleanup();
    mocks.grantRole.mockReset();
    mocks.navigate.mockReset();
    mocks.overview.data = undefined;
  });

  test("offers declared zero-member roles and grants a canonical group subject", async () => {
    mocks.overview.data = overviewData();
    mocks.grantRole.mockResolvedValue({ grant_role: true });
    renderPage(<OverviewPage />);

    fireEvent.click(screen.getAllByRole("button", { name: "Grant" }).at(-1)!);
    fireEvent.click(await screen.findByRole("button", { name: "Choose group" }));
    const submit = screen.getAllByRole("button", { name: "Grant" }).at(-1)!;
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(submit);

    await waitFor(() => expect(mocks.grantRole).toHaveBeenCalledWith({
      subject: "auth/group:7#member",
      role: "angee/role:reader",
    }));
  });

  test("offers only writable roles, excluding derived and legacy rows", async () => {
    mocks.overview.data = overviewData();
    renderPage(<OverviewPage />);

    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Role" }));
    expect(await screen.findByRole("option", { name: "angee / Reader" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "angee / Admin" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "angee / Removed" })).toBeNull();
  });

  test.each([false, undefined])("keeps a refused or missing grant result open for retry (%s)", async (granted) => {
    mocks.overview.data = overviewData();
    mocks.grantRole.mockResolvedValue({ grant_role: granted });
    renderPage(<OverviewPage />);

    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    fireEvent.click(await screen.findByRole("button", { name: "Choose group" }));
    const submit = screen.getAllByRole("button", { name: "Grant" }).at(-1)!;
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(submit);

    expect(await screen.findByText("Could not grant role.")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
  });

  test("links dashboard metrics to their related IAM views", () => {
    mocks.overview.data = overviewData();
    renderPage(<OverviewPage />);

    const users = screen.getByRole("link", { name: /Users/ });
    expect(users.getAttribute("href")).toBe("/iam/users");
    expect(screen.getByRole("link", { name: /Roles/ }).getAttribute("href")).toBe(
      "/iam/roles",
    );
    expect(screen.getByRole("link", { name: /^Grants/ }).getAttribute("href")).toBe(
      "/iam/grants",
    );
    expect(screen.getByRole("link", { name: /Relationships/ }).getAttribute("href")).toBe(
      "/iam/relationships",
    );
    expect(screen.getByRole("link", { name: /^Privileged/ }).getAttribute("href")).toBe(
      "/iam/grants",
    );
    expect(screen.getByRole("link", { name: /^No direct role/ }).getAttribute("href")).toBe(
      "/iam/users",
    );

    fireEvent.click(users);
    expect(mocks.navigate).toHaveBeenCalledWith("/iam/users");
  });
});

function overviewData(): unknown {
  return {
    iam_roles: [
      { id: "angee/role:reader", role_id: "reader", namespace: "angee", label: "Reader", declared: true, grantable: true },
      { id: "angee/role:admin", role_id: "admin", namespace: "angee", label: "Admin", declared: true, grantable: true },
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
    <AppRuntimeProvider
      runtime={{
        icons: baseIcons,
        widgets: defaultWidgets,
        routeHref: createRouteHref(iam.routes ?? []),
      }}
    >
      <ToastProvider>
        <ModalsHost><InAppLinkProvider navigate={mocks.navigate}>{children}</InAppLinkProvider></ModalsHost>
      </ToastProvider>
    </AppRuntimeProvider>,
  );
}
