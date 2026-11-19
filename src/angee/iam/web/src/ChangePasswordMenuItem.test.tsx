// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { DropdownMenu, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";

import { ChangePasswordMenuItem } from "./ChangePasswordMenuItem";

const mocks = vi.hoisted(() => ({ changePassword: vi.fn() }));
vi.mock("@angee/refine", async (original) => ({
  ...await original<typeof import("@angee/refine")>(),
  useAuthoredMutation: () => [mocks.changePassword, { fetching: false, error: null }],
}));
const { Provider, clearClients } = createUiTestProviders({ resources: [] });
afterEach(() => { cleanup(); clearClients(); });
beforeEach(() => mocks.changePassword.mockReset().mockResolvedValue({
  change_password: { ok: true, message: "Password changed." },
}));

test("the native menu opens the shared password dialog, which clears its inputs on close", async () => {
  render(<Provider><ShellPageTestProviders runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>User menu</DropdownMenu.Trigger>
      <DropdownMenu.Portal><DropdownMenu.Positioner><DropdownMenu.Content>
        <ChangePasswordMenuItem />
      </DropdownMenu.Content></DropdownMenu.Positioner></DropdownMenu.Portal>
    </DropdownMenu.Root>
  </ToastProvider></ModalsHost></ShellPageTestProviders></Provider>);
  fireEvent.click(screen.getByRole("button", { name: "User menu" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Change password" }));
  await screen.findByRole("dialog", { name: "Change password" });
  for (const label of ["Current password", "New password", "Confirm new password"]) {
    expect(screen.getByLabelText<HTMLInputElement>(label).type).toBe("password");
  }
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "current-password" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "new-password" } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "mismatch" } });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  await screen.findByText("The passwords do not match.");
  expect(mocks.changePassword).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "new-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  await waitFor(() => expect(mocks.changePassword).toHaveBeenCalledExactlyOnceWith({
    current_password: "current-password", new_password: "new-password",
  }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Change password" })).toBeNull());
  // The menu item stays mounted while its nested form runs. Reopening gets a fresh form owner.
  fireEvent.click(await screen.findByRole("menuitem", { name: "Change password" }));
  await screen.findByRole("dialog", { name: "Change password" });
  expect(screen.getByLabelText<HTMLInputElement>("Current password").value).toBe("");
  expect(screen.getByLabelText<HTMLInputElement>("New password").value).toBe("");
});
