# Folder/tree explorer harmonisation research

## Goal

Harmonise the folder/tree explorer experience across knowledge, storage, and
parties/contact folders without pushing domain policy into a generic component or
copying explorer shell code into each addon.

## Architecture Gate

Owner map:

- `@angee/ui` owns rendered explorer primitives: `Explorer`, `TreeView`,
  `Tree`, `RelationPicker`, `ResourceList`, `ListView`, and the headless
  `useScopedTreeExplorer` root/tree controller.
- Addon web packages own domain projections and verbs: knowledge maps pages to
  page-tree rows and writes pages; storage maps drives/folders/files to navigator
  rows and writes files/folders; parties maps directories/folders/contacts to the
  contacts browse surface.
- Django models/managers own persistence rules: storage folder validity lives on
  `storage.Folder`; knowledge page parenting lives on `knowledge.Page`; parties
  folder semantics currently live on `parties.Folder` as synced address-book
  mirrors.
- GraphQL schema resources own the client contract. Parties `contact_folders`
  are currently read-only, and `Person.folder` is read-only in the person form.
- TanStack Router owns record routes; existing storage/knowledge folder
  selection is local explorer state, not URL state.

Sibling inventory:

- `addons/angee/knowledge/web/src/views/KnowledgePage.tsx` composes
  `Explorer + RelationPicker + TreeView + useScopedTreeExplorer`. Its domain
  code is page-specific: open-page routing, wikilinks, page create/delete/move,
  markdown editor, and backlinks aside.
- `addons/angee/storage/web/src/views/StoragePage.tsx` composes the same shared
  explorer pieces. Its domain code is file-specific: file list scoping, upload
  target, file preview, file move/trash/restore, and folder create/rename/delete.
- `addons/angee/parties/web/src/PeoplePage.tsx` currently composes
  `ResourceList` with a relation `Facet` on `folder`, not a tree explorer.
  The backend `parties.Folder` docstring says manual creation and a folder tree
  are deferred until a create path lands.

Dependency check:

- `docs/stack.md` says React owns rendering, Refine owns resource data hooks and
  cache, TanStack Router owns routing, `react-resizable-panels` owns split panes,
  and `@angee/ui` owns rendered views and headless view state. No new dependency
  is needed.

Thin caller check:

- Addon pages should declare the explorer root, tree rows, selected item, filter,
  and domain verbs. They should not hand-roll tree rendering, resource-list
  mechanics, drag/drop payload decoding, or the repeated navigator layout.

Deletion check:

- With only knowledge and storage, an earlier consistency pass left the pages
  separate because the remaining code was mostly domain-specific. Parties would
  create a third explorer-shaped consumer, so the smallest useful deletion is the
  repeated navigator shell and root/tree wiring, not the whole page.

Naming check:

- Use `folder` for folder-like domain records and `tree` for the rendered
  hierarchy. Keep `Drive`/`Vault`/`Directory` as domain root names; do not rename
  them to a generic root in schema or UI labels.

## Current Shape

The clean shared core already exists:

- `Explorer` is the three-pane resizable layout.
- `TreeView` folds flat parent-keyed rows into `TreeNode`s and wires native
  drag/drop payload handling.
- `Tree` owns keyboard navigation, collapse state, indentation, row selection,
  and tree roles.
- `useScopedTreeExplorer` owns root options, root pinning, tree row selection,
  selected-root hinting, and selection clamping.
- `InlineTextAction` already backs storage and knowledge create/rename controls.

Knowledge and storage still repeat:

- loading/empty state around missing roots;
- the `div.flex h-full flex-col gap-2 p-2` navigator shell;
- root `RelationPicker` wiring;
- `TreeView` wiring with identical `parent`, `rowKey`, `icon`, class shape;
- footer action placement.

The row builders should remain addon-owned:

- `pageTreeRows` sorts folders before notes and picks `note`/`folder` glyphs.
- `folderTreeRows` injects `All files` and `Trash` pseudo-nodes and filters out
  virtual folders.
- A parties row builder would likely inject `All contacts` plus one node per
  synced contact folder, grouped under its directory if/when `Folder.parent` or
  directory-root grouping exists.

## Parties Constraints

Parties is not ready for full storage-style folder management without backend
ownership work:

- `ContactFolderType` exposes name/directory/source fields but no `parent`.
- `_CONTACT_FOLDER_RESOURCE` is read-only: `insert=False`, `update=False`,
  `delete=False`.
- `Person.folder` is projected but not insertable/updatable through the person
  resource, and the form marks it read-only.
- `Folder` is currently a synced address-book mirror keyed by
  `(directory, source_href)`. Manual folders and a nested tree are explicitly
  deferred in the model docstring.

So a first harmonisation should be browse/filter parity only unless the product
decision is to introduce manual contact folder management.

## Recommended Path

1. Add a narrow `ScopedTreeNavigator` or `TreeScopedNavigator` in `@angee/ui`.
   It should compose the existing `RelationPicker`, `TreeView`, and
   `useScopedTreeExplorer` outputs. Keep it presentation/headless-adapter level:
   props for `rootLabel`, `rootOptions`, `rootId`, `onRootChange`, `treeRows`,
   `selectedId`, `onSelect`, `footer`, `emptyState`, and optional DnD props.

2. Keep `Explorer` separate. A whole `TreeScopedExplorer` page component is too
   broad: storage needs upload/list/preview, knowledge needs editor/backlinks,
   and parties needs a resource list. The shared part is the navigator, not the
   entire page.

3. Refactor knowledge and storage to use the navigator component without moving
   their row builders, actions, queries, or page bodies. Expected deletion:
   duplicated navigator shell and root picker/tree boilerplate in both pages.

4. For parties browse-only parity, add a contacts explorer page or evolve
   `PeoplePage` to wrap its existing `ResourceList` in `Explorer` and feed it a
   folder filter from the shared scoped navigator. It should still compose
   `ResourceList`; do not hand-roll a contact list.

5. If the product needs manual contact folders, first extend the backend owner:
   add `parent` only if nested contact folders are real product behavior, add a
   manager/write backend for create/rename/delete/move, expose the fields in the
   parties schema, and only then add navigator footer actions. Do not fake this
   in React while the schema says folders are synced and read-only.

## Suggested Parties Browse Shape

For the first pass:

```text
Explorer autoSave="parties.contacts"
  navigator:
    ScopedTreeNavigator
      root = Directory or "Contacts"
      rows = All contacts + contact folders
      selected folder -> local explorer state
  content:
    ResourceList resource="parties.Person" filter={folder filter}
```

Open questions before implementation:

- Should the explorer live at `/parties/people` by replacing the current people
  list, or should a new `/parties/contacts` route combine all person contacts
  while the current People/Organizations pages stay plain resource pages?
- Should contact folders be grouped by directory as visual roots even before a
  `parent` field exists, or should directory remain a picker/root like
  drive/vault?
- Are organizations meant to be folder-browsable, or are contact folders only
  for synced people from CardDAV?

## Checks For An Implementation Pass

- Unit tests for the new shared navigator: root change, tree selection, footer
  rendering, empty state, and DnD passthrough.
- Existing `KnowledgePage` and `StoragePage` explorer wiring tests updated to
  prove behavior is preserved.
- Parties page test proving folder selection filters the existing
  `ResourceList` rather than bypassing it.
- Typecheck or focused Vitest run for `@angee/ui`, `@angee/knowledge`,
  `@angee/storage`, and `@angee/parties`.
