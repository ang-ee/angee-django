import { test, expect, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

test.use({ storageState: roleStatePath("alice") });

test("edits made while a real save response is delayed remain dirty and saveable", async ({ page, api }) => {
  const title = `Save race ${crypto.randomUUID()}`;
  const created = await api.query<{ insert_notes_one: { id: string } }>(
    "mutation Create($title: String!) { insert_notes_one(object: {title: $title, body: \"Test-owned note\"}) { id } }",
    { title },
  );
  expect(created.errors).toBeUndefined();
  const id = created.data?.insert_notes_one.id;
  expect(id).toBeTruthy();
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  let responseArrived = () => {};
  const arrived = new Promise<void>((resolve) => { responseArrived = resolve; });
  let intercepted = false;
  try {
    const notes = new NotesPage(page);
    await notes.openNote(id!);
    await page.route(/\/graphql\/(console|public)\//, async (route) => {
      const body = route.request().postDataJSON() as { query?: string };
      if (intercepted || !body.query?.includes("update_notes_by_pk")) {
        await route.continue();
        return;
      }
      intercepted = true;
      const response = await route.fetch();
      responseArrived();
      await held;
      await route.fulfill({ response });
    });
    await notes.titleInput.fill(`${title} first`);
    await notes.saveButton.click();
    await arrived;
    await notes.titleInput.fill(`${title} later`);
    release();
    await expect(notes.saveButton).toBeEnabled();
    await expect(notes.titleInput).toHaveValue(`${title} later`);
    const first = await api.query<{ notes_by_pk: { title: string } }>(
      "query Saved($id: String!) { notes_by_pk(id: $id) { title } }", { id },
    );
    expect(first.errors).toBeUndefined();
    expect(first.data?.notes_by_pk.title).toBe(`${title} first`);
    await notes.saveButton.click();
    await expect(notes.saveButton).toHaveCount(0);
    const second = await api.query<{ notes_by_pk: { title: string } }>(
      "query Saved($id: String!) { notes_by_pk(id: $id) { title } }", { id },
    );
    expect(second.errors).toBeUndefined();
    expect(second.data?.notes_by_pk.title).toBe(`${title} later`);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
    if (id) {
      const deleted = await api.query<{ delete_notes_by_pk: { id: string } | null }>(
        "mutation Delete($id: String!) { delete_notes_by_pk(id: $id) { id } }", { id },
      );
      expect(deleted.errors).toBeUndefined();
      expect(deleted.data?.delete_notes_by_pk?.id).toBe(id);
    }
  }
});
