---
"@angee/ui": minor
---

A record form now decides in one place whether its save can write a field: of
the record's form fields, those the model updates on an edit or takes on a
create, and under the form's own submit owner, which receives the whole values,
every field and every path. What the save sends, its resets of hidden fields
included, reads that, as before; so now do the check, whether a field's control
takes edits and whether a create displays the field. A field the save cannot write is no longer
checked, whatever its widget, so a required one left empty no longer blocks the
save, and its control is read-only and no longer marked required. Before, a
field the model did not update showed an editable control on an edit, and a
field outside the create fields one on a create, and the save dropped what was
typed there without a word; a required one left empty blocked the save with its
message under that control.

Whether the record redacts a field is a second fact with one owner. A redacted
field is not checked; its control is its lock, or read-only where a page renders
the control itself, whatever its widget declares; and a server refusal of it
shows in the banner as `name: message`. Before, a redacted required field left
empty blocked the save with "This field is required." under its lock, where the
person has no control. Redaction does not change what is sent: a redacted field
the form itself wrote (a preset's write, a hidden reset) is sent as before, and
one the form only loaded is not, since an edit sends only what changed. Limit,
as before: a structured field goes whole once one of its members changed, so
where the record redacts a structured field and a preset writes one member, the
untouched members go as the placeholders the form loaded.

On a create, a field the save cannot write that holds no value is no longer
displayed at all: not in the sheet, its panes, the status or the body. Before,
such a field showed an editable control whose input the save dropped or, where
it was declared read-only, a bare label with an empty value (a stamp such as
"Created at", a computed status, a number the server gives). A field that holds
a value (a create seed, a list's `createDefaults`, a `defaultValue`, a preset's
write) is displayed read-only, as soon as it holds one and for as long as it
does; it is shown, not sent, since the create input does not take it. An edit
displays every such field read-only, empty or not. A section left with no field
to display shows nothing, and a pane left with none is no longer listed among
the tabs, on a create and on an edit where a condition leaves it none. Limit:
where a value leaves a pane with no field, or gives it one, while the form is
open, and the number of panes thereby changes between one and several, the
remaining pane's content is rebuilt, so a control being typed in there loses
focus, and a pane the person had chosen is selected again when it returns (a
form draws one pane as a plain region and several as tabs). A title the create cannot write is
read-only and shows the create's own heading. An empty status that is read-only on a create although the create input
takes it is not displayed either, as before (a proposal's state, a round's
status, a version-control bridge's lifecycle).

A control a page renders itself (`BoundDescriptorField`) for a path no form field
declares, such as a field only a group the record does not display declares, is
now read-only, where it took typing that the save dropped; the cure is to declare
the field, hidden if need be, as pages that render their own control already do.
Under the form's own submit owner it stays editable, and the owner receives what
is typed there. Limit: such a control for a path the record redacts is read-only
but shows no lock. The lock is `RecordFieldMarkControl`'s restricted mark, which
the form's own field rows render in place of the control (`BoundFieldRow` in
`form-view-body.tsx`); `LabeledDescriptorField`, which `BoundDescriptorField`
renders, shows the field's marks without it. A server refusal of a field the
save cannot write shows in the banner with its own text, as `name: message`,
rather than calling the field highlighted. A record rail row whose field the
metadata lacks or does not read, which was already not shown, is no longer
checked nor saved either. What a form sends is unchanged.

A registered widget declares `writesOwnValue: true` in its widget definition when,
on a saved record, it commits its value through its own action, or not at all,
and decides its own editability. Where the save cannot write the widget's field,
a record form then leaves the widget enabled on a saved record; on a create the
flag changes nothing, and a redacted field's control is read-only whatever the
flag says. The flag is read from the registered definition, so such a widget is
registered eagerly, not through `lazyWidget`. The shared `visibility` widget,
`projects.phase` and `angee.tags.recordTags` declare it.

Upgrade: a widget that writes by itself on a saved record (through its own
action, mutation or dialog rather than the `onChange` it is given) must now
declare `writesOwnValue: true`, or its control turns read-only on an edit where
the save cannot write its field; the rule covers registered widgets only, and a
component a form renders directly (an extra below the form, a group's custom
content) is outside it. A control a page renders itself for a path no form field
declares is now read-only unless the form's own submit owner saves it: declare
the field, hidden if need be. A create no longer displays the empty read-only
rows of fields outside the model's create input, nor a pane that holds only such
rows; a `defaultValue` or a create seed keeps one displayed.
