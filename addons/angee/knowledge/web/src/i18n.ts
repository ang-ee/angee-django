// English fallback strings for the knowledge wiki. The host runtime owns the
// active translations; these are the defaults used when a key is missing.
// Components resolve them through `useKnowledgeT()` (below).

import { useNamespaceT, type MessageVars } from "@angee/sdk";

export const enKnowledgeMessages: Record<string, string> = {
  "knowledge.loading": "Loading knowledge",
  "knowledge.vaults.unavailableTitle": "Knowledge unavailable",
  "knowledge.vaults.emptyTitle": "No vaults",
  "knowledge.vaults.emptyDescription": "No vaults are available to you.",
  "knowledge.vault.label": "Vault",
  "knowledge.vault.placeholder": "Select a vault",
  "knowledge.vault.searchPlaceholder": "Search vaults…",
  "knowledge.page.loading": "Loading page",
  "knowledge.page.notFoundTitle": "Page not found",
  "knowledge.page.notFoundDescription": "This page is no longer available.",
  "knowledge.page.selectTitle": "Select a page",
  "knowledge.page.selectDescription": "Choose a page from the tree to read it.",
  "knowledge.page.crumbFallback": "Page",
  "knowledge.page.deleteConfirmTitle": 'Delete "{title}"?',
  "knowledge.page.deleteConfirmBody":
    "Deleting a folder removes the pages inside it too.",
  "knowledge.page.deleteConfirm": "Delete",
  "knowledge.backlinks.emptyTitle": "No backlinks",
  "knowledge.backlinks.emptyDescription":
    "Pages that link to this one show up here.",
  "knowledge.backlinks.heading": "Backlinks",
  "knowledge.newPage.newNote": "New note",
  "knowledge.newPage.newFolder": "New folder",
  "knowledge.newPage.folderPlaceholder": "Folder name",
  "knowledge.newPage.notePlaceholder": "Note title",
  "knowledge.newPage.titleLabel": "New page title",
  "knowledge.newPage.create": "Create",
  "knowledge.editor.bodyPlaceholder": "Write your page…",
  "knowledge.editor.titlePlaceholder": "Untitled",
  "knowledge.editor.titleLabel": "Page title",
  "knowledge.editor.deleteLabel": "Delete page",
  "knowledge.editor.saving": "Saving…",
  "knowledge.editor.saveFailed": "Save failed",
  "knowledge.editor.saved": "Saved",
  "knowledge.editor.wordCount": "{count} words",
  "knowledge.editor.folderTitle": "Folder",
  "knowledge.editor.folderDescription":
    "A folder groups pages — open a note in the tree to edit it.",
  "knowledge.settings.title": "Vaults",
  "knowledge.settings.description": "The access boundary a tree of pages lives in.",
};

// A translator bound to the `knowledge` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useKnowledgeT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("knowledge", enKnowledgeMessages);
}
