# Tags

A shared labelling vocabulary that reaches every taggable model through one
polymorphic edge. The [models module](models.py) owns the contracts below; its
docstrings carry the exact API.

## Vocabulary

A `Tag` is one label: a name, a colour, an attention flag and an optional
category. A `TagCategory` is a named group of tags in a manual order
(`position`, a fractional rank appended after the last category), with a
description and an optional `max_per_record`. A tag sits in at most one category;
an uncategorised tag stays valid, and deleting a category leaves its tags
uncategorised. `attention` stays a per-tag flag: a list that opts into the
attention column shows the record's attention tags beside its open decisions.

Tags and categories are reference vocabulary. Every signed-in actor reads them;
platform admins and members of `tags/role:tags_admin` curate them
([permissions](permissions.zed): `tags/tag`, `tags/tag_category`). A category
narrows no model: every taggable type can carry any category's tags.

## Taggable types

A taggable type is declared by its owner, never by this addon. The owning addon
depends on `angee.tags`, composes `TaggedModel` on the topmost REBAC-typed model,
declares the type's `target`-backed relation on `tags/tag_assignment` in its
`permissions.extends.zed`, composes `TaggedNode` on its console node and
`tags_input_extensions` on its console inputs ([schema](schema.py)), and places
the `tags` field on its form and list. `TagAssignment.check` (`tags.E001` to
`tags.E003`) fails startup when the composed models and the declared relations
differ. Adding or removing a tag needs the record owner's declared tag authority;
an edge is read by whoever reads both the tag and the record.

## The maximum per record

`TagAssignmentManager.require_category_maximum` is the one check. It counts each
record's edges in a category with a maximum, as named system work, so tags the
actor cannot read count too, and refuses the first record over its category's
maximum with a `ValidationError`. Every write that can raise a record's count
runs it in the write's transaction, so a refusal rolls the write back:

- `attach` (the `tag` mutation) and `set_for` (a record save carrying `tags`)
  lock the target row first, so concurrent tag writes on one record serialize,
  then write and judge the result. The error is keyed `tags`.
- A tag save that writes `category` judges that category's records (`category`).
- A category save that writes `max_per_record` judges its records
  (`max_per_record`). A lowered maximum a record already exceeds is refused.
- A merge into a tag whose category has a maximum refuses merged tags filed under
  another category: file them into the category first, which the tag save judges,
  then merge. A merge within one category only collapses tags.

Queryset and bulk updates, and raw edge creates, bypass these verbs and saves and
are not judged.

## Console

The Tags menu holds the tag vocabulary (filterable by category) and the tag
categories page. The registered tag form edits a tag's category. The
`angee.tags.recordTags` widget, for records whose scalar fields are read-only,
shows one picker per category in category order, then the uncategorised tags; a
tag created from a category's picker is filed under it.

## Seeds

The demo tier seeds neutral categories and files the demo tags under them;
the resource ledger's `(source_addon, xref)` identity makes reloads idempotent.
