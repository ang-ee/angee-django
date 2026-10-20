import { expect, GraphQLClient, requireGraphQLData, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";
import { RecordAside } from "../pages/record-aside";
import { test } from "./fixtures/notes";

test.use({ storageState: roleStatePath("alice") });

test("a scheduled task needs its assignee until completion moves it to Done", async ({ page, note }) => {
  const aside = new RecordAside(page);
  const title = `Review ${note.id}`;
  const consoleApi = new GraphQLClient(page.request, "/graphql/console/");
  let taskId: string | undefined;
  await new NotesPage(page).openNote(note.id);
  await aside.open("Tasks");

  await test.step("schedule through the Tasks pane and show its assignee under Planned", async () => {
    const input = aside.root.getByRole("textbox", { name: "What needs doing", exact: true });
    await input.fill(title);
    // Submit from the field: the toolbar also has a Schedule icon button.
    await input.press("Enter");
    const planned = aside.root.getByRole("region", { name: "Planned", exact: true });
    const row = planned.getByRole("listitem").filter({ hasText: title });
    await expect(row).toBeVisible();
    const { record_tasks } = requireGraphQLData(await consoleApi.query<{
      record_tasks: Array<{ id: string; title: string; assignee: { username: string; display_name: string } | null }>;
    }>(`query ScheduledTask($id: ID!) {
      record_tasks(model_label: "notes.Note", record_id: $id) { id title assignee { username display_name } }
    }`, { id: note.id }));
    const task = record_tasks.find((task) => task.title === title);
    taskId = task?.id;
    expect(taskId).toBeTruthy();
    const assignee = task?.assignee;
    expect(assignee?.username).toBe("alice");
    await expect(row).toContainText(assignee!.display_name || assignee!.username);
    await expect(aside.scope("Planned")).toHaveText(/Planned\s*1/);
  });

  await test.step("the Timeline includes the task; the header opens its Needs you scope", async () => {
    await aside.open("Timeline");
    await expect(aside.line(/Open in Tasks/, title)).toBeVisible();
    await expect(aside.needsYou).toHaveAccessibleName("Needs you · 1");
    await aside.open("Discussion");
    await aside.needsYou.click();
    await expect(page).toHaveURL((url) => url.searchParams.get("chatterTab") === "timeline.rail" &&
      url.searchParams.get("chatterFocus") === "needs-me" &&
      url.searchParams.get("chatterFrom") === "timeline.rail" && !url.searchParams.has("chatterTarget"));
    await expect(aside.tab("Timeline")).toHaveAttribute("aria-selected", "true");
    await expect(aside.scope("Needs you")).toHaveAttribute("aria-pressed", "true");
    await expect(aside.line(/Open in Tasks/, title)).toBeVisible();
    await expect(aside.line(/View change/)).toHaveCount(0);
  });

  await test.step("complete the task; Done gains it and Needs you clears", async () => {
    await aside.line(/Open in Tasks/, title).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("chatterTab") === "projects.tasks" &&
      url.searchParams.get("chatterTarget") === taskId && url.searchParams.get("chatterFocus") === "task" &&
      url.searchParams.get("chatterFrom") === "timeline.rail");
    await expect(aside.tab("Tasks")).toHaveAttribute("aria-selected", "true");
    const planned = aside.root.getByRole("region", { name: "Planned", exact: true });
    await aside.completeTask(title);
    const { record_tasks } = requireGraphQLData(await consoleApi.query<{
      record_tasks: Array<{ id: string; status: string; done_at: string | null }>;
    }>(`query CompletedTask($id: ID!) {
      record_tasks(model_label: "notes.Note", record_id: $id) { id status done_at }
    }`, { id: note.id }));
    const completed = record_tasks.find((task) => task.id === taskId);
    expect(completed?.status).toBe("DONE");
    expect(completed?.done_at).toBeTruthy();
    await expect(aside.scope("Planned")).toHaveText(/Planned\s*0/);
    await expect(planned).toHaveCount(0);
    await aside.scope("Done").click();
    await expect(aside.scope("Done")).toHaveAttribute("aria-pressed", "true");
    await expect(aside.root.getByRole("region", { name: "Done", exact: true })
      .getByRole("listitem").filter({ hasText: title })).toBeVisible();
    await expect(aside.needsYou).toHaveCount(0);
    await aside.open("Timeline");
    await aside.scope("All").click();
    await expect(aside.line(/Open in Tasks/, title)).toContainText("Done");
    await expect(aside.scope("Needs you")).toHaveCount(0);
  });
});
