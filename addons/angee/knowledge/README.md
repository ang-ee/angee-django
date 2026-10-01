# Record pages

Knowledge contributes a **Pages** chatter tab for record routes. It lists
actor-readable page bindings and embeds `KnowledgePageView`. Binding and
unbinding use the existing role-keyed mutations, which require write access to
both the page and target record. A record owner must still contribute its own
`knowledge/record_binding` read arm; the binding grants no access by itself.

The rendered addon exports `recordPagesContribution({ id, label, role, when,
sequence })`. A product addon can add a second tab with a specific binding
role and a route predicate, for example:

```tsx
chatter: [recordPagesContribution({
  id: "reference-pages",
  label: "Reference pages",
  role: "reference",
  when: (context) => context.route?.modelLabel === "example.Record",
})],
```

The configured role is passed to the same binding query and write controls.
The record owner continues to own the permission arm and any route tab policy.

For dated, role-scoped notes, use `RecordNotesStream({ target, role, vault,
heading, composer })` in a record section or `recordNotesContribution({ id,
label, role, vault, heading, composer, when })` in chatter. `vault` is the public
vault id. The stream reads page content through the binding query and offers its
inline composer only when the record can be bound and that vault projects `write`.
It creates a note page, writes its markdown body, then binds it under `role`.
The existing Pages tab keeps its binding and inline reader behavior.

Page access follows the current schema. The former one-shot author-attribution
transition and its management command were removed before any deployment used
them; schema sync is the only policy transition for this addon.
