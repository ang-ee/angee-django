---
"@angee/ui": patch
---

A record form's save no longer writes into the values it reads. Where a form
saves a structured field (an object or JSON field) together with one of its
dotted members, and the member goes on the wire as another value than the form
holds (a cleared number as `null`, a picked record as its id), the payload
builder (`mutationData`) put the structured field's own object into what it
sends and then wrote the member's wire value through it, into the values the
save had read. It now copies what it takes from them (`structuredClone`, as it
already copied a hidden reset). For a form that saves such a pair, two things
change. After a successful save the form is no longer left marked as changed,
with Save and Discard still showing and the cleared or picked value held
against a baseline that recorded its wire value: it is clean and takes the
saved record's values. And a form's own submit owner receives in
`context.values` what the form held, where it received the member's wire
value. No declared form of the framework or of its products saves such a pair
today.

What a form sends is unchanged for a record form's values, which are plain
data: the form's undo history already copies them the same way on every
change. A value of another kind would change: a class instance would be sent as
a plain copy without its methods (a `toJSON` among them), and a value holding a
function would throw, as the undo history already throws when it is set. No
widget or form of the framework or of its products puts such a value in a
record form.
