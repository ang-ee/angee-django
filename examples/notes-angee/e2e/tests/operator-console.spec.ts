import { test, expect, roleStatePath } from "@angee/e2e";

// The operator console is admin-only at the server (the daemon connection is
// null for everyone else). The routes themselves are not UI-gated, so the real
// boundary is what each role sees once the section pane asks for the connection.

const SECTIONS = [
  { label: "Services", path: "/operator/services" },
  { label: "Workspaces", path: "/operator/workspaces" },
  { label: "Sources", path: "/operator/sources" },
  { label: "GitOps", path: "/operator/gitops" },
  { label: "Operations", path: "/operator/operations" },
  { label: "Templates", path: "/operator/templates" },
  { label: "Secrets", path: "/operator/secrets" },
] as const;

test.describe("operator console — admin", () => {
  test.use({ storageState: roleStatePath("admin") });

  test("renders the section nav and navigates across all sections", async ({
    page,
  }) => {
    await page.goto("/operator");
    await expect(page).toHaveURL(/\/operator/);

    // The chrome surfaces the active app's sections as a nav; Overview is the
    // landing section.
    await expect(
      page.getByRole("link", { name: "Overview", exact: true }),
    ).toBeVisible({ timeout: 20000 });

    for (const section of SECTIONS) {
      await page
        .getByRole("link", { name: section.label, exact: true })
        .click();
      await expect(page).toHaveURL(new RegExp(`${section.path}$`));
    }
  });

  test("the Overview settles to the daemon snapshot", async ({ page }) => {
    await page.goto("/operator");
    // The snapshot's git-backed resolvers take a couple seconds; the pane must
    // resolve to the stack summary and never stay on the loading state (a poll
    // firing faster than the response once aborted every request in flight).
    await expect(page.getByText("angee-notes")).toBeVisible({ timeout: 20000 });
    await expect(page.getByText("Loading overview")).toHaveCount(0);
  });

  test("the Sources pane lists the git-backed sources", async ({ page }) => {
    await page.goto("/operator/sources");
    await expect(page.getByText("angee-django")).toBeVisible({ timeout: 20000 });
  });
});

test.describe("operator console — non-admin boundary", () => {
  for (const role of ["alice", "bob"] as const) {
    test.describe(role, () => {
      test.use({ storageState: roleStatePath(role) });

      test(`${role} sees the not-configured boundary, not operator data`, async ({
        page,
      }) => {
        await page.goto("/operator");
        await expect(
          page.getByText(/not configured for this user/i),
        ).toBeVisible({ timeout: 20000 });
      });
    });
  }
});
