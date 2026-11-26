---
"@angee/ui": minor
---

A field descriptor carries the declared format of a value held as text:
`FieldDescriptor.format`, one of `"date"`, `"date-time"` or `"time"`, the JSON
Schema `format` of a string. It is the value's type, never its presentation,
which stays the widget's. A form spec's parser sets it from a schema whose
`string` declares one of those three formats, the backend's form spec among
them (it now keeps a date member's `format` beside the widget that presents
it, the date widget it selects or its author's own, an authored `json` widget
aside; nothing asserts it there, since no validator is handed that spec);
any other format, and a format on another type, is not carried: where a schema
validator runs (a schema dialog, the server) it still reads it from the schema.
A hand-written descriptor may declare it, and an action argument carries it.
Declare it only on a value held as text: on a list or a switch it would make
their empty value `null` too.

What a field holds when it holds nothing reads it first: a descriptor with a
declared `format` holds `null`, whatever its kind and widget, since `""` is no
date, date and time or time; an explicit `string` kind without one still holds
`""`, whatever its widget. Where a `string` with a declared format changed:

| Field | Where | Before | Now |
|---|---|---|---|
| a nullable one holding `null` | the presence control, on a record form and in every dialog | "Set value", which wrote `""` | its own control with "Left empty", and no "Set value" |
| one the record lacks, neither omittable nor presence-required | record form: an edit that changed another member of its object | `""` | `null` |
| a nullable one with no default | record form: a create draft | `""` | `null` |
| one neither required nor nullable | record form: a create draft; a schema dialog; a `MutationDialog` | `""` | left out |
| an item "Add item" adds to a list of them | a `MutationDialog` | `""`: submit enabled | `null`: submit disabled, "This field is required." under the item |
| | a schema dialog | `""`: submit enabled, "Enter a valid date." on a submit attempt | `null`: submit disabled, with no message before a submit attempt, as for a list of integers |
| an optional action argument declaring one under the `date` or the `text` widget, untouched | its `submit` | `""` | `null` |

Of these rows, a member the backend's form spec emits reaches the first and the
two "Add item" rows only: it marks every member omittable or presence-required
and gives a nullable one a `null` default, so a nullable one already seeded
`null`, and one the record lacks is neither seeded nor sent. The other rows are
a hand-written or raw schema's. An
optional action argument under the `datetime` widget already reached `submit`
as `null`.

No declared form of the framework or of its products has such a member. A
`string` with only a date or time widget and no declared format keeps `""`. A
date drawn as a text input (a raw schema's with no authored widget, or one
under an authored `text` widget) seeds `null` where it is seeded at all, and
clearing it writes `""`, which a schema dialog's validator refuses as before.

Upgrade: a hand-written descriptor that carries a date, a date and time or a
time as text declares its `format` (`kind: "string", format: "date"`), or it
holds `""` when it holds nothing.
