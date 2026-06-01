import { test, expect } from "@angee/e2e";

import { LoginPage } from "../pages/login-page";

// Runs anonymously (no storageState) — this exercises the real login UI, not the
// API shortcut the setup project uses.
test("logs in through the UI and lands on notes", async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.signIn("alice", "alice");

  await expect(page).toHaveURL(/\/notes/);
  await expect(page.getByText("Welcome to Angee")).toBeVisible();
});
