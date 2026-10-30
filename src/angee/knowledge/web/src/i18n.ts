// English fallback strings for the knowledge wiki. The host runtime owns the
// active translations; these are the defaults used when a key is missing.
// Components resolve them through `useKnowledgeT()` (below).

import { createNamespaceT } from "@angee/ui";

export const enKnowledgeMessages: Record<string, string> = {
  "record.loading": "Loading bound pages",
  "record.empty": "No pages bound",
  "record.choosePage": "Choose a page",
  "record.bind": "Bind",
  "record.unbind": "Unbind",
  "record.writeError": "Could not change the page binding.",
  "record.loadError": "Could not load the bound pages.",
  "record.untitledPage": "Untitled page",
  "notes.heading": "Notes",
  "notes.loading": "Loading notes",
  "notes.empty": "No notes yet.",
  "notes.prompt": "Add note",
  "notes.add": "Add note",
  "notes.added": "Note added.",
  "notes.bodyRequired": "Write a note before posting.",
  "notes.createError": "Could not create the note.",
  "notes.loadError": "Could not load the notes.",
  "notes.vaultError": "Could not reach the notes vault.",
  "vaults.unavailableTitle": "Knowledge unavailable",
  "vaults.emptyTitle": "No vaults",
  "vaults.emptyDescription": "No vaults are available to you.",
  "nav.label": "Pages",
  "vault.label": "Vault",
  "vault.placeholder": "Select a vault",
  "vault.searchPlaceholder": "Search vaults…",
  "page.loading": "Loading page",
  "page.loadError": "Could not load this page.",
  "page.selectTitle": "Select a page",
  "page.selectDescription": "Choose a page from the tree to read it.",
  "page.notFoundTitle": "Page not found",
  "page.notFoundDescription": "This page is unavailable or you do not have access to it.",
  "newPage.menu": "New",
  "newPage.page": "Page",
  "newPage.folder": "Folder",
  "newPage.subpage": "Subpage",
  "page.moveError": "Could not move this page.",
  "nav.noMatches": "No pages match",
  "vault.settings": "Vault settings",
  "folder.emptyTitle": "No pages here yet",
  "folder.emptyDescription": "Pages in this folder appear here.",
  "newPage.folderPlaceholder": "Folder name",
  "newPage.notePlaceholder": "Note title",
  "newPage.titleLabel": "New page title",
  "newPage.create": "Create",
  "editor.titleLabel": "Page title",
  "editor.saveFailed": "Save failed",
  "vault.clone": "Create from template",
  "vault.cloneName": "New vault name",
  "vault.cloneFailed": "Could not create the vault from this template.",
  "settings.title": "Settings",
  "settings.description": "The access boundary a tree of pages lives in.",
};

// A translator bound to the `knowledge` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useKnowledgeT = createNamespaceT("knowledge", enKnowledgeMessages);
