# Record pages

The Knowledge navigator also serves mounted page collections. A mount's
`defaultFilter` for `knowledge.Page` is translated by the shared resource query
owner and applied to the pages read. The navigator offers vaults represented in
that collection and uses the same page editor, hierarchy and permissions. New
pages are created in the selected vault; creating an unrelated vault is offered
only in the unscoped wiki.

Every binding write locks and rechecks the canonical target through `upsert`, so
deleted targets refuse writes. `RecordBinding.on_target_delete` declares cascade
teardown through the shared record-pointer lifecycle; deleting a target removes
its bindings without changing their Page or Vault owners.

MCP exposes vault-scoped `create_page` through the same GraphQL tool compiler as
`read_page` and `append_to_page`; vault write grants remain the boundary.
Retrieval backends implement `search_many` over each bounded readable vault group
and return a `RetrievalResult` containing actor-scoped `hits` and visible
`failures`. The bound vault's `retrieval.search` uses the same contract.
`search_pages` preserves its page projection; `search_passages` exposes the full
result through GraphQL and MCP, including degradation when a provider arm fails.
The optional pgvector addon supplies hybrid passage, link and graph retrieval;
lexical backends return page-only hits.

Knowledge contributes a **Pages** group to the Related tab of record routes. It lists
actor-readable page bindings as `PaneRow` links to each page's reader. Binding and
unbinding use the existing role-keyed mutations, which require write access to
both the page and target record. A record owner declares its type bindable from
its `permissions.extends.zed` — one relation backed by the binding's `target`,
unioned into `target_read` and `target_write`:

```zed
definition knowledge/record_binding {
    relation record: example/record // rebac:field=target
    permission target_read = record->read
    permission target_write = record->write
}
```

A record owner knowledge itself depends on cannot name knowledge, so knowledge
declares its arm in its own `permissions.extends.zed`: a party (a person or an
organization) is bindable, read through the party's `read` and written through
its `write`. An undeclared type cannot be bound under an actor and its bindings stay
unreadable; the binding grants no access by itself.

## The people vault

Knowledge's install tier provisions one vault for people, `Vault.PEOPLE`
(`knowledge.vault_people`, resolved by `Vault.objects.for_resource`). It is
unowned: platform admins administer it (read, write, delete, share and
transfer) and members of knowledge's `vault_admin` and `knowledge_admin` roles
read, write and delete it, as on every vault; a platform admin may transfer it
to a person, who then owns it. Nobody else reads it until a deployment grants
it, with a vault's ordinary grants: an operator shares it as viewers with a user
or a group's members through the vault's share surface (`viewer` is the vault's
grantable relation, managed by whoever may `share` it), and grants `editor`
administratively, through the Permissions Hub or a consumer addon's
`kind = "grants"` install resource (`{resource: knowledge.vault_people,
relation: editor, subject: <a user's xref, or auth/group:<id>#member>}`).
Knowledge's own vault roles reach it too.

A person's pages sit in a folder page bound to their party under the `folder`
role (`RecordBindingManager.party_folder`), with named notes below it
(`PageManager.subpage`); the binding places the folder on the person's record
and grants nothing. **Whoever can read the people vault reads every person's
pages, including the pages of people they cannot read in parties**, and whoever
can write it writes them; finer control between readers is a later version, by
ruling. Each verb locks first (the party, or the folder), so two requests make
one folder or one note. A person makes them by the vault's permissions and the
binding's (write on the page and on the party); an import, in a system context,
makes them as system work (`PageManager.create_in` without an actor). A page's
title is unique in its vault, so a caller names a folder and a note uniquely,
with the person's public id for instance. After a merge the survivor holds both
folders: the oldest is its folder, no third is made, and nothing is lost.

An importer writes a source's text about a person with
`RecordBindingManager.write_sourced_page`, passing the text it last wrote there
(a directory mapping reads it from its link with `DirectoryMapping.applied`).
The same text writes nothing. A changed text replaces a note that still holds
what the source last wrote, compare-and-set on its body hash. A note a person
has edited since is never written over: the newer text is appended below their
text, quoted under the line "Newer text from the source, not written over this
page's edits:", for them to take or leave, and the source's record history
keeps it too. A note stays when the mapping or the link goes. Integrate's
record conflicts are not used for this: on a pull-only stream a conflict
resolves only by keeping the source's side, whose re-application would find the
edit again, and it would mark the person's whole contact failed. The Pages group shows only on
record views of declared types (their resource metadata lists
`knowledge.RecordBinding` in `recordEdges`), and `record_knowledge_can_bind`
offers binding only on a declared type the actor can write.

The group is the `related#groups/knowledge.pages` child of the framework's
Related tab. The rendered addon exports `recordPagesGroup({ label, role, sequence, when })`,
which returns a Related group child. A product addon can declare a specific binding
role on its own model, for example:

```tsx
containers: {
  "notes.Note#groups": {
    "notes.reference-pages": recordPagesGroup({ label: "Reference pages", role: "reference" }),
  },
},
```

The framework checks `edge.model` against `recordEdges`. Knowledge declares the
configured role as its own `bindingRole` content and uses it for writes. All groups
observe one cached binding read with `role: null` per record and partition it on
the client. The generic Pages group excludes roles claimed
by eligible sibling groups on that record; it shows `related` and other unclaimed
roles. Counts use the same selection, so a Contract binding appears and counts
once. Model scope, container narrowing and each group's `when` determine which
role claims apply. The record owner continues to own the permission arm and
narrowing of the aside.

Bind uses `PaneLabelPopover` with the native searchable `RelationPicker` and Bind button.
Candidates are paged on the server, writable, untrashed, and not already bound
under the chosen role. Binding requires an explicit selection. One group
component owns the picker and rows through `render(context, label)`, sharing
busy state with Unbind. The picker closes on success and shows binding failures
inside it. Generic Bind writes `related` and is absent if a sibling claims that
role; a role-specific group writes its configured role. Both groups include view-as restrictions.

Pages compose the native `Form` with title and Markdown body fields. `FormView`
owns the record header and toolbar, Save/Discard, validation, dirty state and
navigation protection. Knowledge's submit adapter only translates the guarded
body mutation into that form contract; title and body persist together through
`MarkdownPageManager.write_body`. Its accepted response advances the exact typed
page cache and native form baseline before derived reads finish refreshing;
live invalidation and reconnect still refresh relations and permissions. Folders
use the generated page update. Notes destinations use a public vault ID or a
canonical resource handle (`vault: { resource: "addon.name.xref" }`), never a
display name; the Knowledge owner resolves the ledger and rechecks current access.
Backlinks and Bound to are Related groups using the shared pane rows; Bound to
uses `RecordReferenceRow` for bounded labels and registered record links.

The body uses the existing `markdown.editor` widget and shared Markdown renderer.
The navigator composes `CollectionTreeView` for server-scoped roots, lazy child
pages, search and paging. Direct record reads stay independent of that navigator;
readable ancestors reveal the path without exposing hidden parent labels. The
record pager replays the same branch, order and filter through native route context.
The existing **New** menu owns Page, Folder and Subpage creation; child lists
use native record links. `LinkManager` owns exact-title wikilink resolution,
including target creation, rename and trash/restore; the UI renders its readable targets.
Markdown links using a page's `wikilink_url` share that index and navigation;
literal links inside code are excluded.

For dated, role-scoped notes, use `RecordNotesStream({ target, role, vault,
heading, composer })` in a record section or `recordNotesTab({ label, role,
vault, heading, composer, sequence, when })` as an aside child; the tab shows
where the Pages group does. `vault` is the public vault id. The stream reads page content through the binding query and offers its inline
composer only when the record can be bound and that vault projects `write`.
`RecordBindingManager.create_note` creates the page, body and binding in one
transaction. Its changeset receipt makes a transport retry idempotent.
The Pages group continues to link to each page's reader.

Pages are trashable through the shared `trash_record` / `restore_record` verbs,
authorized by page `delete`. Trashing a page trashes the untrashed pages below it
with the same stamp; Zed withholds trashed pages from everyone who cannot delete
them, so they leave lists, counts, search, backlinks and wikilinks. The navigator
lists each trashed subtree once under "Removed (n)"; restoring it brings back the
pages trashed with it, never a page trashed on its own.

Page access follows the current schema. The former one-shot author-attribution
transition and its management command were removed before any deployment used
them; schema sync is the only policy transition for this addon.

Pages compose the shared hierarchy for parent moves and subtree scopes, and their
Comments pane reads through the page policy. Vault removal uses trash: its pages
are withheld while preserving their own removal flags, so restoring a vault does
not restore separately removed pages. RecordBinding retargets retain history for
merge reversal.
