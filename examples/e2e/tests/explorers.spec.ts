import { expect, GraphQLClient, requireGraphQLData, roleStatePath, test } from "@angee/e2e";

import { ExplorerPane } from "../pages/explorer-pane";

test.use({ storageState: roleStatePath("admin") });

test("explorer pane toolbars create a file folder and open a new knowledge note in its reader", async ({ page }) => {
  const consoleApi = new GraphQLClient(page.request, "/graphql/console/");
  const suffix = crypto.randomUUID();
  const folderName = `Explorer folder ${suffix}`;
  const noteTitle = `Explorer note ${suffix}`;
  let pageId: string | undefined;
  const { drives, vaults } = requireGraphQLData(await consoleApi.query<{
    drives: Array<{ id: string; name: string }>;
    vaults: Array<{ id: string; name: string }>;
  }>(`query ExplorerRoots {
    drives(limit: 1) { id name }
    vaults(where: { is_trashed: { _eq: false } }, limit: 1) { id name }
  }`));
  expect(drives[0], "The full-profile seed must include a drive").toBeDefined();
  expect(vaults[0], "The full-profile seed must include a vault").toBeDefined();
  try {
    await test.step("the Files pane toolbar carries the drive picker and creates a folder node", async () => {
      await page.goto("/storage");
      const files = new ExplorerPane(page, "Files");
      await files.reveal();
      await files.selectRoot(drives[0]!.name);
      await files.create("folder", folderName);
      await expect(files.node(folderName)).toBeVisible();
    });

    await test.step("the Knowledge pane toolbar creates a note and opens its reader", async () => {
      await page.goto("/knowledge");
      const pages = new ExplorerPane(page, "Pages");
      await pages.reveal();
      await pages.selectRoot(vaults[0]!.name);
      await pages.create("note", noteTitle);
      await expect(page).toHaveURL(/\/knowledge\/[^/?]+/);
      pageId = new URL(page.url()).pathname.split("/").at(-1);
      await expect(pages.node(noteTitle)).toBeVisible();
      const reader = page.getByRole("article").filter({ has: page.getByRole("heading", { name: noteTitle, exact: true }) });
      await expect(reader).toBeVisible();
      await expect(reader.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    });
  } finally {
    if (pageId) requireGraphQLData(await consoleApi.query(`mutation RemoveExplorerPage($id: ID!) {
      delete_page(id: $id, confirm: true) { has_blockers total_deleted_count }
    }`, { id: pageId }));
    const { folders } = requireGraphQLData(await consoleApi.query<{ folders: Array<{ id: string }> }>(
      `query ExplorerFolder($name: String!, $drive: String!) {
        folders(where: { name: { _eq: $name }, drive: { _eq: $drive } }) { id }
      }`, { name: folderName, drive: drives[0]!.id },
    ));
    for (const folder of folders) requireGraphQLData(await consoleApi.query(`mutation RemoveExplorerFolder($id: ID!) {
      delete_folder(id: $id, confirm: true) { has_blockers total_deleted_count }
    }`, { id: folder.id }));
  }
});
