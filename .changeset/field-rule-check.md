---
"@angee/ui": minor
---

A record form and a dialog now read the `required` flag of a field with a
presence flag, a template or a bound, and of a member of an object field, as
presence: such a field or member that is required and left out (absent, or a
`null` it does not allow) now says "This field is required." under it. A record
form on a create also counts the model's required-on-create names for such a
field. Before, only a presence-required value, or one whose key was held with
`undefined` or a `null` it does not allow, was refused; a required member that
is not presence-required, as a hand-written FormSpec object's `required` makes
it, was accepted, and a `MutationDialog` sent the object without it. An item of
a list is always present, so no list item's verdict changes. The backend's own
FormSpec projections mark every required property presence-required, so a form
built from one refuses nothing new. A blank text, a text of blanks, an empty
list and an empty object remain values, as before: the field's own bounds judge
them, and an empty text under a minimum length of one, in a field neither
omittable nor nullable, still says it is required. At the opening, a
`MutationDialog` keeps Save enabled while a required member of an object field
that is not itself required is left out, and shows the message under the member
when Save is pressed; it disables Save, with no message, while such a member of
a required object field, or a required field that may be left unset
(`omittable`), is left out. An `ActionFormDialog` without a resolver disables its
submit at the opening, with no message: a missing member's message shows once
the person edits another member of its object field, and a submission that
reaches the form anyway shows each message. A record form shows the message when
the person saves. A dialog whose definition brings a resolver, such as the one
`jsonSchemaActionArgs` returns, now refuses such a value before its resolver,
with the message the resolver gave.

The client no longer checks these values a person cannot see or change; the
server answers for them, with its own message. A record form no longer checks a
field whose descriptor is hidden, a field read-only as resolved for the form's
values (by its own flag, its `resolve`, `createOnly` on an edit or `editOnly` on
a create), nor a field outside the fields its save writes for the record (one in
a section the record's implementation does not admit). Where such a field held a
value the record form refused, the save used to stop, with nothing on screen for
a hidden field or one outside those fields; a required field with a presence
flag, a template or a bound that was not presence-required was not refused when
left out or blank, and was saved as it was. On every surface, a member of an
object field or an item of a list that is hidden or read-only is not checked,
nor anything under it: one that is presence-required and left out, holds its key
with `undefined` or a `null` it does not allow, or breaks a bound, an empty text
under a minimum length of one included, was refused, a hidden one with nothing
on screen, and is now accepted. A dialog reads a field's key holding `undefined`
as absent, as a record form does, so a field that is bounded or templated,
neither required, presence-required nor `omittable`, and opened that way no
longer says it is required; a member holding its key with `undefined` still says
it is required.

Where the record form still checks, or no longer checks, a field against that
rule: a redacted required field left empty still blocks the save, with "This
field is required." under its lock, where the person has no control; a required
field the record cannot write still shows an editable
control, blocks the save while it is empty, and the save drops what is typed
there; a field declared hidden whose control its page renders itself (with
`BoundDescriptorField`) is no longer checked by the client, and a server refusal
of it shows under that control and in the banner; and a control a page renders
for a field outside the fields the save writes is no longer checked, while what
is typed there is dropped by the save, as before.

A server refusal of a record-form field whose descriptor is hidden, that lies
outside the fields the save writes, or that its condition hides now shows in the
banner with its own text, as `name: message` under the field's raw name rather
than its label, the way a refusal of an undeclared field does. Before, the
banner said "Please fix the highlighted fields: …" with nothing highlighted and
without the server's text, and next to a form-level error only that error
showed. A shown field's refusal is unchanged, and a field declared hidden whose
control its page renders shows the refusal under that control as well as in the
banner. What a form saves or sends is unchanged.
Upgrade: `structuredFieldErrors` returns `required` for a field left out at its
own path that is required but not presence-required, where it returned nothing
(its own `required` flag decides by default, or the new last parameter
`required`), and for a required member left out that is not presence-required;
it returns nothing at or under a member or list item that is hidden or
read-only, where it reported that value's presence and bounds.
