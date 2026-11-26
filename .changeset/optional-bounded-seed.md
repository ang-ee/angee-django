---
"@angee/ui": patch
---

A form no longer refuses its own seed. An optional field with a bound (or
`omittable`, or a template) whose empty value is `null`, and whose declared
type does not refuse that `null`, was seeded with it and then refused by the
rule check as "This field is required.", because a held `null` the field does
not allow counted against it for holding the key. That is a field declared by a
number widget, or a date or time widget, alone, or one of kind `any`: a record
form's create and its edit of a record that lacks the field were refused under
an optional field the person had not touched; a `MutationDialog` was refused
the same way, or showed its submit disabled with no message beside a required
sibling. The same held one level down, for a member of an object field whose
empty value is `null` and whose type does not refuse it, bound or not.

The rule that judges a held value (`structuredFieldErrors`, at the field's own
path and at each member's) now composes the seed rule: the `null` a form gives
a field that holds nothing (its `emptyValueForField`, which `refusesSeed` does
not refuse) is not held against the field for holding the key. Not changed: a
required field and a presence-required one answer for a `null` as before
(refused, unless the field may be null, where `null` is a value); a value
outside its bound is refused; a field whose own declared type refuses `null` (a
typed FormSpec number that is not nullable) is not seeded with it and is still
refused when it holds one; a `null` that is not the field's own empty value
(a text field holding `null`) is still judged; and a list item that holds
nothing is still refused, whatever seeded it, since an item has no key to
leave out.

At a field's own path no declared form has the shape: no descriptor bounds a
field without a kind. A member needs no bound to have it: an untyped member of
an object field, which the server's form projection emits for a JSON field,
was refused when it held `null`.

Known limit: a record form's edit of a record that lacks such a field as a
member of a JSON value sends the member as `null`, as it already does for the
same field without a bound. A server member typed as a plain number with a
default accepts the key left out and refuses `null`; a field over such a member
declares its kind, and is then not seeded with `null` on a create or in a
dialog.
