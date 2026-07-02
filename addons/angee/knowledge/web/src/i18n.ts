// English fallback strings for the knowledge wiki. The host runtime owns the
// active translations; these are the defaults used when a key is missing.
// Components resolve them through `useKnowledgeT()` (below).

import { createNamespaceT } from "@angee/ui";

export const enKnowledgeMessages: Record<string, string> = {
  "loading": "Loading knowledge",
  "vaults.unavailableTitle": "Knowledge unavailable",
  "vaults.emptyTitle": "No vaults",
  "vaults.emptyDescription": "No vaults are available to you.",
  "nav.label": "Pages",
  "vault.label": "Vault",
  "vault.placeholder": "Select a vault",
  "vault.searchPlaceholder": "Search vaults…",
  "page.loading": "Loading page",
  "page.notFoundTitle": "Page not found",
  "page.notFoundDescription": "This page is no longer available.",
  "page.selectTitle": "Select a page",
  "page.selectDescription": "Choose a page from the tree to read it.",
  "page.crumbFallback": "Page",
  "page.deleteConfirmTitle": 'Delete "{title}"?',
  "page.deleteConfirmBody":
    "Deleting a folder removes the pages inside it too.",
  "page.deleteConfirm": "Delete",
  "backlinks.emptyTitle": "No backlinks",
  "backlinks.emptyDescription":
    "Pages that link to this one show up here.",
  "backlinks.heading": "Backlinks",
  "newPage.newNote": "New note",
  "newPage.newFolder": "New folder",
  "newPage.folderPlaceholder": "Folder name",
  "newPage.notePlaceholder": "Note title",
  "newPage.titleLabel": "New page title",
  "newPage.create": "Create",
  "editor.bodyPlaceholder": "Write your page…",
  "editor.titlePlaceholder": "Untitled",
  "editor.titleLabel": "Page title",
  "editor.deleteLabel": "Delete page",
  "editor.saving": "Saving…",
  "editor.saveFailed": "Save failed",
  "editor.saved": "Saved",
  "editor.wordCount": "{count} words",
  "editor.folderTitle": "Folder",
  "editor.folderDescription":
    "A folder groups pages — open a note in the tree to edit it.",
  "settings.title": "Vaults",
  "settings.description": "The access boundary a tree of pages lives in.",
};

// A translator bound to the `knowledge` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useKnowledgeT = createNamespaceT("knowledge", enKnowledgeMessages);
