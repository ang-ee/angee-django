import { expect, requireGraphQLData, test as base } from "@angee/e2e";

export const NOTES_QUERY = `query Notes {
  notes { id title }
  notes_aggregate { aggregate { count } }
}`;

export interface NotesData {
  notes: { id: string; title: string }[];
  notes_aggregate: { aggregate: { count: number } };
}

/** A fresh audited record: seed notes may have no creation history or old tasks. */
export const test = base.extend<{ note: NotesData["notes"][number] }>({
  note: async ({ api }, use) => {
    const title = `Record standard ${crypto.randomUUID()}`;
    const { insert_notes_one: note } = requireGraphQLData(await api.query<{
      insert_notes_one: NotesData["notes"][number];
    }>(`mutation CreateRecordNote($title: String!) {
      insert_notes_one(object: { title: $title, body: "Record-page coverage" }) { id title }
    }`, { title }));
    try {
      await use(note);
    } finally {
      const removed = requireGraphQLData(await api.query<{
        delete_notes_by_pk: { id: string } | null;
      }>(`mutation DeleteRecordNote($id: String!) {
        delete_notes_by_pk(id: $id) { id }
      }`, { id: note.id }));
      expect(removed.delete_notes_by_pk?.id).toBe(note.id);
    }
  },
});
