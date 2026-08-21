export const NOTES_QUERY = `query Notes {
  notes { id title }
  notes_aggregate { aggregate { count } }
}`;

export interface NotesData {
  notes: { id: string; title: string }[];
  notes_aggregate: { aggregate: { count: number } };
}
