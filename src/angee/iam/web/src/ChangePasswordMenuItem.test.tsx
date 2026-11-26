// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { ModalsHost, ToastProvider, UserMenu, defaultWidgets } from "@angee/ui";
import { containersFromChildren } from "@angee/ui/runtime";
import { createUiTestProviders } from "@angee/ui/testing";

import { ChangePasswordMenuItem } from "./ChangePasswordMenuItem";

const mocks = vi.hoisted(() => ({ changePassword: vi.fn() }));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(),
  useActionOutcomeMutation: () => [mocks.changePassword, { fetching: false, error: null }],
}));
vi.mock("@tanstack/react-router", async (original) => ({
  ...await original<typeof import("@tanstack/react-router")>(), useNavigate: () => vi.fn(),
}));
// Under a router, as an app page is: a form dialog guards route moves while it holds work.
const { Provider, clearClients } = createUiTestProviders({ resources: [], router: true });
afterEach(() => { cleanup(); clearClients(); });
beforeEach(() => mocks.changePassword.mockReset().mockResolvedValue({ ok: true, message: "Password changed." }));

const containers = containersFromChildren([{ address: "shell#user-menu" }], {
  "shell#user-menu": { "iam.change-password": { content: <ChangePasswordMenuItem /> } },
});
function renderMenu(hasUsablePassword?: boolean, preview = false) {
  const user = { id: "self", name: "Ada", hasUsablePassword };
  return render(<Provider><ShellPageTestProviders runtime={{
    widgets: defaultWidgets, containers,
    auth: { status: "authenticated", user, hasRole: () => false, viewAs: preview ? {
      viewAs: { userId: user.id }, currentUser: user, realUser: { id: "admin", name: "Admin" },
      viewablePeople: [], enter: vi.fn(), exit: vi.fn(),
    } : undefined },
  }}><ModalsHost><ToastProvider><UserMenu /></ToastProvider></ModalsHost></ShellPageTestProviders></Provider>);
}

test.each([true, false, undefined])("usable-password fact %s hides the item only when explicitly false", async (usable) => {
  renderMenu(usable);
  fireEvent.click(screen.getByRole("button", { name: "User menu" }));
  await screen.findByRole("menu");
  expect(screen.queryByRole("menuitem", { name: "Change password" }) !== null).toBe(usable !== false);
});

test.each([true, false, undefined])("view-as never offers Change password (usable: %s)", async (usable) => {
  renderMenu(usable, true);
  fireEvent.click(screen.getByRole("button", { name: "User menu" }));
  await screen.findByRole("menu");
  expect(screen.queryByRole("menuitem", { name: "Change password" })).toBeNull();
  expect(mocks.changePassword).not.toHaveBeenCalled();
});

test("the native menu opens the shared password dialog, which clears its inputs on close", async () => {
  renderMenu(true);
  const trigger = screen.getByRole("button", { name: "User menu" });
  fireEvent.click(trigger);
  const item = await screen.findByRole("menuitem", { name: "Change password" });
  expect(item.getAttribute("aria-haspopup")).toBe("dialog");
  fireEvent.click(item);
  await screen.findByRole("dialog", { name: "Change password" });
  await waitFor(() => expect(trigger.getAttribute("aria-expanded")).toBe("false"));
  // A person who opens the dialog and types writes the current password.
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Current password")));
  for (const label of ["Current password", "New password", "Confirm new password"]) {
    expect(screen.getByLabelText<HTMLInputElement>(label).type).toBe("password");
    expect(screen.getByLabelText<HTMLInputElement>(label).autocomplete).toBe(label === "Current password" ? "current-password" : "new-password");
  }
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "current-password" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "mismatch" } });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  await screen.findByText("The passwords do not match.");
  expect(mocks.changePassword).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "new-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  await waitFor(() => expect(mocks.changePassword).toHaveBeenCalledExactlyOnceWith("", {
    current_password: "current-password", new_password: "new-password",
  }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Change password" })).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  // The menu closes while the dialog remains alive; reopening gets a fresh form owner.
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByRole("menuitem", { name: "Change password" }));
  await screen.findByRole("dialog", { name: "Change password" });
  expect(screen.getByLabelText<HTMLInputElement>("Current password").value).toBe("");
  expect(screen.getByLabelText<HTMLInputElement>("New password").value).toBe("");
});
