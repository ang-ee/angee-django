---
"@angee/ui": minor
---

A record form's fields are now the form's own and those of the groups it displays
for the record. A group the form does not show no longer brings its fields into
what the form checks and saves, nor among the fields its banner calls
highlighted: a `savedOnly` group on a create, or a contributed section whose
permission the record lacks (and every such section on a create, which has no
record), as a row-decided container child the record does not admit already did
not; nor does a record rail row behind its group's, its child's or its own
permission that the record lacks. Before, a required field left empty in a
`savedOnly` group on a create, in a contributed section whose permission the
record lacked, or in such a rail row, blocked the save with nothing on screen; a
`savedOnly` group's field was sent on a create, a text left empty as an empty
text, and a create seed (`defaultValues`, a list's `createDefaults`) or a
field's `defaultValue` for a field only such a group declares was sent too; and
a server refusal of such a field was named among the highlighted fields. Now the
save goes ahead without the field and sends nothing for it, and the banner still
names the field, with the server's own text for its refusal, as `name: message`,
without calling it highlighted; no control is marked invalid unless a page
renders one for the field itself, which shows the message.

The form keeps its own declarations and order: a field the page declares outside
its own groups stays, with the page's declaration, whatever a contribution
re-declares; a field of a displayed page group keeps the page's declaration; a
field only contributed groups declare comes with the first displayed group's
declaration, at that group's place; and a field of a page group the form does not
display, which a displayed contribution also declares, comes with the
contribution's declaration. Where the form displays every group, its fields are
exactly what they were. A page's own field that only a contribution the form
does not display re-declares now shows among the form's ungrouped fields, with
its message under it when it fails a rule. Before, it showed nowhere unless it
was the form's title, status or body, though it was still checked and saved: a
required one left empty blocked the save with nothing on screen. Every group's
section keeps its key, order and sequence; where that field is the page's only
ungrouped one, the ungrouped section now comes in front of them. Besides that
field and the rail row named under Limits, what a form shows changes only in its
title, status and body, which are chosen among the record's form fields: a field
only an undisplayed group declares is no longer one, on a create for a
`savedOnly` group, on a record lacking a section's permission, and on an edit
while its record loads, so a status or body declared only in a permission-gated
section now appears when the record arrives.

A control a page renders itself for a field only an undisplayed group declares
is read-only, and nothing is sent for it, unless the form's own submit owner
saves it (before, what was typed there was sent: a `savedOnly` group's field on a
create, a permission-gated section's field on a create or on a record lacking
the permission). Limits: a record rail row that
shares its name with a field only an undisplayed section declares now decides its
own visibility, where that section's declaration decided it.
Upgrade: check every create seed and `defaultValue` for a field that only a
`savedOnly` group or a permission-gated section declares, since it is no longer
sent (declare the field where the create shows it, or let the server set it);
and on a record whose form does not display such a group, its field is no
longer a candidate for the form's title, status or body.
