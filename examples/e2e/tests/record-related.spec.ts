import { expect, GraphQLClient, requireGraphQLData, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";
import { RecordAside } from "../pages/record-aside";
import { test } from "./fixtures/notes";

test.use({ storageState: roleStatePath("admin") });

test("Related uploads a file, follows its record link and binds a knowledge page", async ({ page, note }) => {
  test.setTimeout(60_000);
  const notes = new NotesPage(page);
  const aside = new RecordAside(page);
  const consoleApi = new GraphQLClient(page.request, "/graphql/console/");
  const filename = `record-${note.id}.txt`;
  let fileId: string | undefined;
  let pageId: string | undefined;
  try {
    await notes.openNote(note.id);
    await aside.open("Related");
    await test.step("upload through the Files group and follow the file row", async () => {
      const upload = aside.root.getByRole("button", { name: "Upload", exact: true });
      await expect(aside.groupWithVerb("Upload")).toContainText(/^Files/);
      await upload.click();
      const picker = page.getByRole("dialog", { name: "Upload", exact: true });
      // File inputs have no implicit ARIA role; their accessible label owns this selector.
      const input = picker.getByLabel("Choose files", { exact: true });
      await expect(input).toBeEnabled();
      await input.setInputFiles({
        name: filename, mimeType: "text/plain", buffer: Buffer.from(`Attached to ${note.title}`),
      });
      await page.keyboard.press("Escape");
      const file = aside.root.getByRole("link", { name: new RegExp(filename.replace(".", "\\.")) });
      await expect(file).toBeVisible();
      const href = await file.getAttribute("href");
      expect(href).toMatch(/^\/storage\/[^/?]+/);
      fileId = new URL(href!, page.url()).pathname.split("/").at(-1);
      await file.click();
      await expect(page).toHaveURL((url) => url.pathname === `/storage/${fileId}`);
      await expect(page.getByRole("heading", { name: filename, exact: true })).toBeVisible();
      await expect(aside.tab("Timeline")).toBeVisible();
      // Files have history and reverse attachments, but no chatter or task edge.
      await expect(aside.tab("Discussion")).toHaveCount(0);
      await expect(aside.tab("Tasks")).toHaveCount(0);
    });

    await test.step("the file's reverse attachment group links back to the note", async () => {
      await aside.open("Related");
      const attachedTo = aside.root.getByRole("listitem").filter({ hasText: /^Attached to(?:\s*·\s*\d+)?$/ });
      await expect(attachedTo).toBeVisible();
      await expect(aside.root.getByRole("link", { name: note.title, exact: true }))
        .toHaveAttribute("href", `/notes/${note.id}`);
    });

    await test.step("bind a knowledge page through Pages", async () => {
      const { vaults } = requireGraphQLData(await consoleApi.query<{ vaults: Array<{ id: string }> }>(
        "query BindingVault { vaults(where: { is_trashed: { _eq: false } }, limit: 1) { id } }",
      ));
      expect(vaults[0], "The full-profile seed must include a knowledge vault").toBeDefined();
      const title = `Bound page ${note.id}`;
      const { create_page } = requireGraphQLData(await consoleApi.query<{ create_page: { id: string } }>(
        `mutation BindingPage($vault: ID!, $title: String!) {
          create_page(vault: $vault, title: $title, kind: "note") { id }
        }`, { vault: vaults[0]!.id, title },
      ));
      pageId = create_page.id;
      await notes.openNote(note.id);
      await aside.open("Related");
      const bind = aside.root.getByRole("button", { name: "Bind", exact: true });
      await expect(aside.groupWithVerb("Bind")).toContainText(/^Pages/);
      await bind.click();
      const picker = page.getByRole("dialog", { name: "Bind", exact: true });
      await picker.getByRole("combobox", { name: "Choose a page", exact: true }).click();
      await page.getByRole("option", { name: title, exact: true }).click();
      await picker.getByRole("button", { name: "Bind", exact: true }).click();
      await page.keyboard.press("Escape");
      await expect(aside.root.getByRole("link", { name: new RegExp(title) }))
        .toHaveAttribute("href", `/knowledge/${pageId}`);
    });
  } finally {
    if (pageId) requireGraphQLData(await consoleApi.query(`mutation RemoveBindingPage($id: ID!) {
      delete_page(id: $id, confirm: true) { has_blockers total_deleted_count }
    }`, { id: pageId }));
    if (fileId) requireGraphQLData(await consoleApi.query(`mutation RemoveAttachedFile($id: ID!) {
      purge_file(id: $id)
    }`, { id: fileId }));
  }
});
