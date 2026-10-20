import { expect, GraphQLClient, requireGraphQLData, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";
import { RecordAside } from "../pages/record-aside";
import { test } from "./fixtures/notes";

test.use({ storageState: roleStatePath("alice") });

test("a note's context strip opens a creation change and returns from its deep link", async ({ page, note }) => {
  const notes = new NotesPage(page);
  const aside = new RecordAside(page);
  // Timeline labels the logical changeset's action, not an individual row's
  // history type. Identify the creating boundary through its authorized history.
  const { history } = requireGraphQLData(await new GraphQLClient(page.request, "/graphql/console/").query<{
    history: { entries: Array<{ id: string }> };
  }>(`query CreatingChangeset($id: ID!) {
    history(model: "notes.Note", id: $id) { entries { id } }
  }`, { id: note.id }));
  expect(history.entries).toHaveLength(1);
  const changesetId = history.entries[0]!.id;
  const creation = aside.line(/View change/);
  await notes.openNote(note.id);
  await aside.reveal();

  await test.step("the strip contains the record-wide capabilities in order", async () => {
    await expect(aside.root.getByRole("tab")).toHaveText([
      /^Timeline/, /^Discussion/, /^Tasks/, /^Related/,
    ]);
    await expect(aside.root.getByRole("tab", { name: "History", exact: true })).toHaveCount(0);
  });

  await test.step("View change opens the creation changeset with a Timeline return strip", async () => {
    await aside.open("Timeline");
    await expect(creation).toHaveCount(1);
    await creation.click();
    await expect(page).toHaveURL((url) =>
      url.searchParams.get("chatterTab") === "provenance.history" &&
      url.searchParams.get("chatterTarget") === changesetId &&
      url.searchParams.get("chatterFocus") === "changeset" &&
      url.searchParams.get("chatterFrom") === "timeline.rail",
    );
    await expect(aside.backToTimeline).toBeVisible();
    await expect(aside.currentHistoryEntry).toBeVisible();
    await expect(aside.root.getByRole("cell", { name: note.title, exact: true })).toBeVisible();
    // Reload proves the four shell keys restore the drill-in without in-memory navigation state.
    await page.reload();
    await expect(aside.backToTimeline).toBeVisible();
    await expect(aside.currentHistoryEntry).toBeVisible();
    await aside.backToTimeline.click();
    await expect(aside.tab("Timeline")).toHaveAttribute("aria-selected", "true");
    await expect(creation).toBeVisible();
    await expect(page).toHaveURL((url) => url.searchParams.get("chatterTab") === "timeline.rail" &&
      ["chatterTarget", "chatterFocus", "chatterFrom"].every((key) => !url.searchParams.has(key)));
  });

  await test.step("a history link without a target is unavailable and still offers Back", async () => {
    await page.goto(`/notes/${note.id}?chatterTab=provenance.history`);
    await expect(aside.root.getByRole("heading", { name: "Nothing selected", exact: true })).toBeVisible();
    await expect(aside.root.getByText("Open this from a line on the Timeline.", { exact: true })).toBeVisible();
    await expect(aside.backToTimeline).toBeVisible();
    await aside.backToTimeline.click();
    await expect(creation).toBeVisible();
  });

  await test.step("an unsaved note has no record capabilities", async () => {
    await page.goto("/notes/new");
    await expect(notes.titleInput).toHaveValue("");
    await expect(aside.root).toHaveCount(0);
  });
});
