import { test, expect, roleStatePath } from "@angee/e2e";

// The IAM/Identity admin console: the menu, the grant composer, the permission
// graph, and the admin gate for non-admins. Read-only/non-destructive.

const SECTIONS = [
  { label: "Users", path: "/iam/users" },
  { label: "Roles", path: "/iam/roles" },
  { label: "Grants", path: "/iam/grants" },
  { label: "Relationships", path: "/iam/relationships" },
  { label: "Schema", path: "/iam/schema" },
] as const;

test.describe("iam console — admin", () => {
  test.use({ storageState: roleStatePath("admin") });

  test("navigates the Identity menu across every section", async ({ page }) => {
    await page.goto("/iam");
    await expect(page).toHaveURL(/\/iam/);
    await expect(
      page.getByRole("link", { name: "Overview", exact: true }),
    ).toBeVisible({ timeout: 20000 });

    for (const section of SECTIONS) {
      await page.goto(section.path);
      // Some sections append a default-group query (e.g. ?group=namespace).
      await expect(page).toHaveURL(new RegExp(`${section.path}(\\?|$)`));
      await expect(page.getByText(section.label, { exact: true }).first()).toBeVisible({
        timeout: 20000,
      });
    }
  });

  test("the Overview grant composer gates the Grant button", async ({
    page,
  }) => {
    await page.goto("/iam");
    await expect(page.getByText("Grant access")).toBeVisible({ timeout: 20000 });
    const principal = page.getByLabel("Principal");
    await expect(principal).toBeVisible();
    await expect(page.getByLabel("Role")).toBeVisible();

    // The composer grants a real platform role, so this test only exercises the
    // button gating — it never submits. Grant stays disabled until a principal
    // is chosen (a role is preselected); picking one enables it.
    const grant = page.getByRole("button", { name: "Grant", exact: true });
    await expect(grant).toBeDisabled();
    await principal.click();
    await page
      .getByRole("option")
      .filter({ hasText: /bob/ })
      .first()
      .click();
    await expect(grant).toBeEnabled();
  });

  test("the Schema view renders the permission graph", async ({ page }) => {
    await page.goto("/iam/schema");
    await expect(page.locator(".react-flow__node").first()).toBeVisible({
      timeout: 20000,
    });
    expect(await page.locator(".react-flow__node").count()).toBeGreaterThan(0);
  });
});

test.describe("iam console — non-admin boundary", () => {
  for (const role of ["alice", "bob"] as const) {
    test.describe(role, () => {
      test.use({ storageState: roleStatePath(role) });

      test(`${role} is denied the admin users list`, async ({ page }) => {
        await page.goto("/iam/users");
        // Admin-gated: a non-admin gets an empty/denied state, never user rows.
        await expect(
          page.getByText(
            /could not load|no records|not configured|unavailable|denied|permission/i,
          ),
        ).toBeVisible({ timeout: 20000 });
      });
    });
  }
});
