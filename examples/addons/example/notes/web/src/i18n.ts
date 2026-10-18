// English message bundle for the `notes` namespace. Components resolve these
// through `useNotesT()` (below); the addon manifest contributes the bundle under
// `i18n.notes`. Keys are dotted by surface. Metadata-driven field/column labels
// live in the SDL, not here — only bespoke component copy is routed.

import { createNamespaceT } from "@angee/ui";

export const enNotesMessages: Record<string, string> = {
  "empty.title": "No notes yet",
  "empty.description": "The agent isn't running yet — provision it to start chatting.",
  "empty.setupAssistant": "Set up your assistant",
  "form.details": "Details",
  "form.owner": "Owner",
  "form.reminder": "Reminder",
  "record.star": "Star",
  "record.versions": "Versions",
  "status.synced": "Synced",
  "status.new": "New note",
  "status.editing": "Editing note",
  "status.all": "All notes",
  "status.version_one": "{count} version",
  "status.version_other": "{count} versions",
  "demo.forgotPassword": "Forgot your password?",
  "demo.logins": "Demo logins",
};

export const useNotesT = createNamespaceT("notes", enNotesMessages);
export type NotesT = ReturnType<typeof useNotesT>;
