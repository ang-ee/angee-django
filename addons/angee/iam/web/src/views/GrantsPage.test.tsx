// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";
import { AppRuntimeProvider, ToastProvider, baseIcons } from "@angee/ui";

import { GrantsPage } from "./GrantsPage";

interface GrantRow {
  id: string;
  principal_id: string;
  principal_ref: string;
  principal_label: string;
  role: string;
  role_name: string;
  namespace: string;
}

const GRANT_ROWS = vi.hoisted(() => ({
  value: [
    {
      id: "grant-a",
      principal_id: "usr_1",
      principal_ref: "auth/user:1",
      principal_label: "Alice",
      role: "angee/role:writer",
      role_name: "Writer",
      namespace: "angee",
    },
    {
      id: "grant-b",
      principal_id: "usr_2",
      principal_ref: "auth/user:2",
      principal_label: "Bob",
      role: "angee/role:reader",
      role_name: "Reader",
      namespace: "angee",
    },
  ] as GrantRow[],
}));

const sdkMocks = vi.hoisted(() => ({
  revoke_role: vi.fn(),
  revokeState: { fetching: false, error: null as Error | null },
}));

// Revoke rides `useAuthoredMutation`; drive it directly so the test controls
// success/failure and pending timing without a live transport.
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [sdkMocks.revoke_role, sdkMocks.revokeState],
}));

// Keep the real feedback owners (`useToast`, `errorMessage`, `Button`, `Code`)
// and stub only the two heavy owners: `ListView` (its data layer belongs to the
// framework's own tests — here it renders the page's columns over fixed rows so
// the per-row action column is exercised) and `useConfirm` (auto-accept the
// revoke gate, as sibling page tests do).
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useConfirm: () => async () => true,
    ListView: ({
      columns,
    }: {
      columns: readonly {
        field: string;
        render?: (row: GrantRow) => unknown;
      }[];
    }) => (
      <div data-testid="grants-list">
        {GRANT_ROWS.value.map((row) => (
          <div key={row.id} data-testid={`grant-row-${row.id}`}>
            {columns.map((column, index) => (
              <div key={index}>
                {column.render
                  ? (column.render(row) as ReactNode)
                  : String(row[column.field as keyof GrantRow] ?? "")}
              </div>
            ))}
          </div>
        ))}
      </div>
    ),
  };
});

describe("IAM grants page", () => {
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
    sdkMocks.revoke_role.mockReset();
    sdkMocks.revokeState.fetching = false;
    sdkMocks.revokeState.error = null;
  });

  test("surfaces a failed revoke as a danger toast", async () => {
    sdkMocks.revoke_role.mockRejectedValue(new Error("SpiceDB unreachable"));

    renderGrants();
    fireEvent.click(revokeButton("grant-a"));

    // The failure title + the underlying error ride the shared danger toast (a
    // dismissible portal, matched in the visible toast and its live region) — no
    // page-local Alert band. `findAllByText` accepts the toast's duplicate.
    expect(
      (await screen.findAllByText("Role was not revoked")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("SpiceDB unreachable").length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(sdkMocks.revoke_role).toHaveBeenCalledWith({
        principal_id: "usr_1",
        role: "angee/role:writer",
      }),
    );
  });

  test("surfaces a domain-false result as a danger toast", async () => {
    sdkMocks.revoke_role.mockResolvedValue({ revoke_role: false });

    renderGrants();
    fireEvent.click(revokeButton("grant-a"));

    expect(
      (await screen.findAllByText("Role was not revoked")).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText("Could not revoke role.").length,
    ).toBeGreaterThan(0);
  });

  test("gates the other rows' actions while one revoke is pending", async () => {
    // A revoke that never settles keeps the page in its pending state.
    sdkMocks.revoke_role.mockReturnValue(new Promise(() => undefined));
    sdkMocks.revokeState.fetching = true;

    renderGrants();
    fireEvent.click(revokeButton("grant-a"));

    // The pending row shows its own busy affordance; every other row's action is
    // disabled until it settles.
    await waitFor(() =>
      expect(revokeButton("grant-a").hasAttribute("data-pending")).toBe(true),
    );
    expect(revokeButton("grant-b").hasAttribute("disabled")).toBe(true);
  });
});

function renderGrants(): ReturnType<typeof render> {
  return render(
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <ToastProvider>
        <GrantsPage />
      </ToastProvider>
    </AppRuntimeProvider>,
  );
}

function revokeButton(rowId: string): HTMLElement {
  return within(screen.getByTestId(`grant-row-${rowId}`)).getByRole("button", {
    name: "Revoke",
  });
}
