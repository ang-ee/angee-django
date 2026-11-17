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
record's edges in a category with a maximum, as named system work, and refuses the
first record over its category's maximum with a `ValidationError`. Archived tags,
tags in an archived category, and tags the actor cannot read all count. Each write
below runs it inside its own transaction, after the write, so a refusal rolls the
write back.

| write | when it is judged | what is judged | error key |
|---|---|---|---|
| a new edge (`TagAssignment.save` on insert): the `tag` verb, a record's `tags` input, a changeset revert's restore, a resource load, `objects.create` | always | that record, in the new tag's category | `tags` |
| a tag save (`Tag.save`) | it writes a `category` that differs from the stored row's | the records holding the tag, in its new category | `category` |
| a category save (`TagCategory.save`) | it lowers `max_per_record` below the stored value | every record holding the category's tags | `max_per_record` ("records already hold more") |
| a merge (`pre_merge`, `TagAssignmentManager.judging_fold`) | after the fold's moves, inside the merge | a tag survivor: the records holding it, in its category; a taggable survivor: its record, in the categories of the tags it gained | none |

Nothing else is judged: a rename, a description, a raised or removed maximum,
unfiling a tag, or a merge into an uncategorised tag. A record already over a
maximum keeps every other edit. `set_for` removes before it adds, so a swap within
a maximum succeeds.

Not judged: `bulk_create` of edges, and queryset `update` or `bulk_update` of
`Tag.category` or `TagCategory.max_per_record`. They bypass the saves above.

Locks keep concurrent writes from judging each other's uncommitted state:

- **Edge writes:** an edge write locks its record's row, then its tags' rows
  (`no_key`, in key order), and reads each tag's category under those locks.
- **Category-side writes:** a refiling or a lowered maximum locks the category row
  (a full row lock, which filing a new tag under it waits on), then the category's
  tags (`no_key`, in key order), before it reads the stored value
  (`TagCategory.lock_filing`).
- **Merges:** a merge holds its records' locks (a tag merge, the survivor tag's)
  and locks the tags a taggable survivor gained before it judges.
- **Result:** whichever write runs second waits for the first and judges its
  committed state. The cost is that every tagging of one tag serializes on that
  tag's row, and every tagging of one record on that record's row.

## Console

The Tags menu holds the tag vocabulary (filterable by category) and the tag
categories page. The registered tag form edits a tag's category. The
`angee.tags.recordTags` widget, for records whose scalar fields are read-only,
shows a labelled group of pickers:

- one per category, in category order, with its maximum when it has one, then the
  uncategorised tags;
- each held tag's category is read once and kept, so pickers never regroup on a
  refetch;
- a category past the category list's first page still gets a picker when the
  record holds one of its tags;
- at a maximum of one, a new pick replaces the held tag, untagging first;
- a tag created from a category's picker is filed under it.

## Seeds

The demo tier seeds neutral categories and files the demo tags under them. The
categories set no maximum, so demo data loads on every host, including one whose
own rules require a capped category to have an owner. The resource ledger's
`(source_addon, xref)` identity makes reloads idempotent.
