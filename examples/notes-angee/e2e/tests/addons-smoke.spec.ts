import { test, expect, roleStatePath, type Page } from "@angee/e2e";

interface AddonRouteCase {
  addon: string;
  path: string;
  expectText: string | RegExp;
}

const ROUTES: readonly AddonRouteCase[] = [
  { addon: "notes", path: "/notes", expectText: "Word Count" },

  { addon: "agents", path: "/agents", expectText: "Demo Agent" },
  { addon: "agents", path: "/agents/templates", expectText: "No records." },
  { addon: "agents", path: "/agents/skills", expectText: "Description" },
  { addon: "agents", path: "/agents/sources", expectText: "Last Synced At" },
  { addon: "agents", path: "/agents/mcp-servers", expectText: "angee-demo" },
  { addon: "agents", path: "/agents/mcp-tools", expectText: "Enabled" },
  { addon: "agents", path: "/agents/providers", expectText: "Anthropic" },
  { addon: "agents", path: "/agents/models", expectText: "Model Use" },

  { addon: "iam", path: "/iam", expectText: "Grant access" },
  { addon: "iam", path: "/iam/users", expectText: "alice@example.com" },
  { addon: "iam", path: "/iam/roles", expectText: "namespace" },
  { addon: "iam", path: "/iam/grants", expectText: "Principal" },
  { addon: "iam", path: "/iam/relationships", expectText: "Resource Ref" },
  { addon: "iam", path: "/iam/schema", expectText: "Inference Model" },

  { addon: "integrate", path: "/integrate", expectText: "Implementation" },
  { addon: "integrate", path: "/integrate/vendors", expectText: "Acme Integrations" },
  { addon: "integrate", path: "/integrate/webhooks", expectText: "Target Url" },
  { addon: "integrate", path: "/integrate/vcs", expectText: "local (active)" },
  { addon: "integrate", path: "/integrate/repositories", expectText: "angee-django" },
  { addon: "integrate", path: "/integrate/sources", expectText: "template" },
  { addon: "integrate", path: "/integrate/templates", expectText: "workspace" },
  { addon: "integrate", path: "/integrate/providers", expectText: "Apexive SSO" },
  { addon: "integrate", path: "/integrate/accounts", expectText: "Provider Label" },
  { addon: "integrate", path: "/integrate/credentials", expectText: "local-templates" },

  { addon: "knowledge", path: "/knowledge", expectText: "Handbook" },
  { addon: "knowledge", path: "/knowledge/settings", expectText: "Handbook" },

  { addon: "operator", path: "/operator", expectText: "notes-angee" },
  { addon: "operator", path: "/operator/services", expectText: "frontend" },
  { addon: "operator", path: "/operator/workspaces", expectText: "No workspaces." },
  { addon: "operator", path: "/operator/sources", expectText: "framework" },
  { addon: "operator", path: "/operator/gitops", expectText: "Unpushed" },
  { addon: "operator", path: "/operator/operations", expectText: "Stack lifecycle" },
  { addon: "operator", path: "/operator/templates", expectText: "claude-code" },
  { addon: "operator", path: "/operator/secrets", expectText: "Has value" },

  { addon: "platform", path: "/platform", expectText: "service_template" },
  { addon: "platform", path: "/platform/models", expectText: "Resource type" },
  { addon: "platform", path: "/platform/fields", expectText: "Relation target" },
  { addon: "platform", path: "/platform/addons", expectText: "Angee" },
  { addon: "resources", path: "/platform/resources", expectText: "Install" },

  { addon: "storage", path: "/storage", expectText: "Drop files here" },
  { addon: "storage", path: "/storage/settings", expectText: "Local filesystem" },
];

const REPOSITORY_DETAIL =
  "/integrate/repositories/UmVwb3NpdG9yeVR5cGU6cmVwb1VrTFdaZzlE";

test.describe("addon route smoke", () => {
  test.use({ storageState: roleStatePath("admin") });

  for (const route of ROUTES) {
    test(`${route.addon}: ${route.path}`, async ({ page }) => {
      const issues = captureBrowserIssues(page);

      await gotoAuthenticatedRoute(page, route.path);

      expect(new URL(page.url()).pathname).toBe(route.path);
      await expect(page.getByText("Something went wrong!")).toHaveCount(0);
      await expect(page.getByRole("main")).toContainText(route.expectText, {
        timeout: 20_000,
      });
      await issues.settled();
      expect(issues.messages()).toEqual([]);
    });
  }

  test("integrate repository detail renders read-only without an update root", async ({ page }) => {
    const issues = captureBrowserIssues(page);

    await gotoAuthenticatedRoute(page, REPOSITORY_DETAIL);

    expect(new URL(page.url()).pathname).toBe(REPOSITORY_DETAIL);
    await expect(page.getByText("Something went wrong!")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "angee-django" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("VCS BRIDGE")).toBeVisible();
    await expect(page.getByRole("button", { name: /^Save$/ })).toHaveCount(0);
    await issues.settled();
    expect(issues.messages()).toEqual([]);
  });

  test("integrate template sources sync discovered templates", async ({ page }) => {
    const issues = captureBrowserIssues(page);

    await gotoAuthenticatedRoute(page, "/integrate/templates");

    await expect(page.getByRole("heading", { name: "Template sources" })).toBeVisible({
      timeout: 20_000,
    });
    const templateSources = page.locator("section").filter({
      has: page.getByRole("heading", { name: "Template sources" }),
    });
    const sourceRow = templateSources.getByRole("row").filter({ hasText: "templates" }).first();
    await sourceRow.click();
    await page.getByRole("button", { name: "Actions" }).click();
    await page.getByRole("menuitem", { name: "Sync templates" }).click();

    await expect(page.getByText("LAST SYNCED AT").last().locator("xpath=..")).toContainText(
      /[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2} (AM|PM)/,
      {
        timeout: 20_000,
      },
    );
    await expect(page.locator("body")).toContainText("agent-default", {
      timeout: 20_000,
    });
    await issues.settled();
    expect(issues.messages()).toEqual([]);
  });
});

async function gotoAuthenticatedRoute(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
  if (new URL(page.url()).pathname !== "/login") return;
  await page.goto(path);
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
}

function captureBrowserIssues(page: Page) {
  const messages: string[] = [];
  const responseChecks: Promise<void>[] = [];

  page.on("pageerror", (error) => {
    messages.push(`pageerror: ${error.message}`);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      messages.push(`console: ${message.text()}`);
    }
  });
  page.on("response", (response) => {
    if (!response.url().includes("/graphql/")) return;
    responseChecks.push(
      response.text().then((body) => {
        if (response.status() >= 400 || body.includes('"errors"')) {
          messages.push(`graphql ${response.status()}: ${body.slice(0, 800)}`);
        }
      }).catch(() => undefined),
    );
  });

  return {
    messages: () => messages,
    settled: async () => {
      await new Promise((resolve) => setTimeout(resolve, 250));
      await Promise.all(responseChecks);
    },
  };
}
