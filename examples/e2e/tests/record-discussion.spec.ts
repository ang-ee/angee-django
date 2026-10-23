import { expect, GraphQLClient, requireGraphQLData, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";
import { RecordAside } from "../pages/record-aside";
import { test } from "./fixtures/notes";

test.use({ storageState: roleStatePath("alice") });

test("a discussion filters messages, marks incoming comments read and follows a Timeline link", async ({ page, browser, note }) => {
  test.setTimeout(60_000);
  const aside = new RecordAside(page);
  const consoleApi = new GraphQLClient(page.request, "/graphql/console/");
  const comment = `Comment ${note.id}`;
  const internalNote = `Internal note ${note.id}`;
  const incoming = `Reply ${note.id}`;
  const notes = new NotesPage(page);
  await notes.openNote(note.id);

  await test.step("the header bell follows and unfollows the record", async () => {
    // Creating a threaded record subscribes its author.
    await expect(aside.follow).toHaveAccessibleName("Following");
    await expect(aside.follow).toHaveAttribute("aria-pressed", "true");
    await expect(aside.follow).toBeInViewport();
    const followBox = await aside.follow.boundingBox();
    const shareBox = await notes.shareButton.boundingBox();
    expect(followBox).not.toBeNull();
    expect(shareBox).not.toBeNull();
    expect(Math.abs(followBox!.y - shareBox!.y)).toBeLessThan(8);
    await aside.follow.click();
    await expect(aside.follow).toHaveAccessibleName("Follow");
    await expect(aside.follow).toHaveAttribute("aria-pressed", "false");
    // Receive the other actor's comment so Mark read has a real unread badge.
    await aside.follow.click();
    await expect(aside.follow).toHaveAccessibleName("Following");
    await expect(aside.follow).toHaveAttribute("aria-pressed", "true");
  });

  await aside.open("Discussion");
  await test.step("post a comment and an internal note; scope chips filter both ways", async () => {
    await aside.post(comment);
    await aside.post(internalNote, "Note");
    await aside.scope("Comments").click();
    await expect(aside.scope("Comments")).toHaveAttribute("aria-pressed", "true");
    await expect(aside.message(comment)).toBeVisible();
    await expect(aside.message(internalNote)).toHaveCount(0);
    await aside.scope("Notes").click();
    await expect(aside.scope("Notes")).toHaveAttribute("aria-pressed", "true");
    await expect(aside.message(internalNote)).toBeVisible();
    await expect(aside.message(comment)).toHaveCount(0);
    await aside.scope("All").click();
  });

  await test.step("the search icon reveals a working search", async () => {
    const search = aside.root.getByRole("searchbox", { name: "Search comments", exact: true });
    await expect(search).toHaveCount(0);
    await aside.root.getByRole("button", { name: "Search comments", exact: true }).click();
    await expect(search).toBeVisible();
    await search.fill(comment);
    await expect(aside.message(comment)).toBeVisible();
    await expect(aside.message(internalNote)).toHaveCount(0);
    await aside.root.getByRole("button", { name: "Search comments", exact: true }).click();
    await expect(search).toHaveCount(0);
    await expect(aside.message(internalNote)).toBeVisible();
  });

  await test.step("Mark read clears an unread badge from another actor", async () => {
    const admin = await browser.newContext({ storageState: roleStatePath("admin") });
    try {
      const result = requireGraphQLData(await new GraphQLClient(admin.request, "/graphql/console/").query<{
        post_record_message: { error: string | null; message: { id: string } | null };
      }>(`mutation IncomingComment($id: ID!, $body: String!) {
        post_record_message(input: { model_label: "notes.Note", record_id: $id, body: $body, kind: COMMENT }) {
          error message { id }
        }
      }`, { id: note.id, body: incoming }));
      expect(result.post_record_message.error).toBeNull();
      expect(result.post_record_message.message?.id).toBeTruthy();
    } finally {
      await admin.close();
    }
    await expect(aside.message(incoming)).toBeVisible();
    const markRead = aside.root.getByRole("button", { name: "Mark read", exact: true });
    await expect(markRead).toHaveText("1");
    await markRead.click();
    await expect(markRead).toHaveCount(0);
    const { record_thread } = requireGraphQLData(await consoleApi.query<{
      record_thread: { unread_count: number };
    }>(`query ReadReceipt($id: ID!) {
      record_thread(input: { model_label: "notes.Note", record_id: $id }) { unread_count }
    }`, { id: note.id }));
    expect(record_thread.unread_count).toBe(0);
  });

  await test.step("the comment's Timeline verb opens Discussion focused on that comment", async () => {
    const { record_thread } = requireGraphQLData(await consoleApi.query<{
      record_thread: { messages: Array<{ id: string; preview: string }> };
    }>(`query CommentTarget($id: ID!) {
      record_thread(input: { model_label: "notes.Note", record_id: $id }) { messages { id preview } }
    }`, { id: note.id }));
    const target = record_thread.messages.find((message) => message.preview === comment)?.id;
    expect(target).toBeTruthy();
    // A retained Notes filter must not conceal the comment named by the link.
    await aside.scope("Notes").click();
    await expect(aside.message(comment)).toHaveCount(0);
    await aside.open("Timeline");
    const line = aside.line(/Open in Discussion/, comment);
    await expect(line).toBeVisible();
    await line.click();
    await expect(page).toHaveURL((url) =>
      url.searchParams.get("chatterTab") === "messaging.discussion" &&
      url.searchParams.get("chatterTarget") === target &&
      url.searchParams.get("chatterFocus") === "message" &&
      url.searchParams.get("chatterFrom") === "timeline.rail",
    );
    await expect(aside.tab("Discussion")).toHaveAttribute("aria-selected", "true");
    await expect(aside.message(comment)).toBeInViewport();
  });
});
