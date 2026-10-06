# Record pages

Knowledge contributes a **Pages** chatter tab for record routes. It lists
actor-readable page bindings and embeds `KnowledgePageView`. Binding and
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

An undeclared type cannot be bound under an actor and its bindings stay
unreadable; the binding grants no access by itself.

The tab is the `record#aside/knowledge.pages` child. The rendered addon exports
`recordPagesTab({ label, role, sequence, when, aliases })`, which returns a
chatter tab child. A product addon can add a second tab with a specific binding
role on its own model's aside, for example:

```tsx
containers: {
  "notes.Note#aside": {
    "notes.reference-pages": recordPagesTab({ label: "Reference pages", role: "reference" }),
  },
},
```

The configured role is passed to the same binding query and write controls.
The record owner continues to own the permission arm and any narrowing of the
aside.

For dated, role-scoped notes, use `RecordNotesStream({ target, role, vault,
heading, composer })` in a record section or `recordNotesTab({ label, role,
vault, heading, composer, sequence, when })` as an aside child. `vault` is the public
vault id. The stream reads page content through the binding query and offers its
inline composer only when the record can be bound and that vault projects `write`.
It creates a note page, writes its markdown body, then binds it under `role`.
The existing Pages tab keeps its binding and inline reader behavior.

Pages are trashable through the shared `trash_record` / `restore_record` verbs,
authorized by page `delete`. Trashing a page trashes the untrashed pages below it
with the same stamp; Zed withholds trashed pages from everyone who cannot delete
them, so they leave lists, counts, search, backlinks and wikilinks. The navigator
lists each trashed subtree once under "Removed (n)"; restoring it brings back the
pages trashed with it, never a page trashed on its own.

Page access follows the current schema. The former one-shot author-attribution
transition and its management command were removed before any deployment used
them; schema sync is the only policy transition for this addon.
