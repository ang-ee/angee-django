import { test, expect } from "@angee/e2e";

import { LoginPage } from "../pages/login-page";
import { NotesPage } from "../pages/notes-page";

// Runs anonymously (no storageState) — this exercises the real login UI, not the
// API shortcut the setup project uses.
test("logs in through the UI and lands on a populated notes list", async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.signIn("alice", "alice");

  await expect(page).toHaveURL(/\/notes/);
  // Resilient post-login signal: the data view actually rendered its scoped
  // list (the pager shows a non-zero total), not a specific seeded note title.
  const notes = new NotesPage(page);
  await expect(notes.recordsLabel).toHaveAttribute(
    "aria-label",
    /\/\s*[1-9][\d,]*/,
    { timeout: 25000 },
  );
});
