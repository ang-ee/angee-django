---
"@angee/ui": minor
---

What a form field holds when it holds nothing is now decided by one rule, which
the record form's draft and seeds, the action, mutation and schema dialogs and
the presence control all read; the record form's own rule is gone. First match
wins: a field that declares a `format` (a date, a date and time or a time
carried as text) holds `null`; an explicit `string` kind holds `""`, whatever its widget; the number
widgets (`integer`, `float`, `percent`, `progressBar`) and the `date`,
`datetime` and `time` widgets hold `null`; the list widgets
(`tagInput`, `list`, `rows`) and a relation list hold `[]`; the `switch` widget
holds `false`, the `object` widget `{}`, and the `json` widget `[]` for an
`array` kind, `null` for an untyped value and `{}` otherwise; then the kind
decides (`array` `[]`, `object` `{}`, `boolean` or `switch` `false`, a number or an untyped
value `null`, or `""` under the `select` widget); anything else holds `""`. A
lines editor's new cell is not a form field and keeps its own rule. Not
changed, and known: the `boolean`, `booleanToggle` and `booleanBadge` widgets
and the `money` widget still hold `""` when no kind is declared, so an optional
argument declared by one of them alone still reaches `submit` as `""`. A
boolean waits for a field's type to reach its descriptor, where a kind decides
it: holding `null`, the presence control would show an unchecked box with "Left
empty", unlike the `switch` widget and a `boolean` kind, which hold `false`. The
`money` widget, which the money addon's fragment registers, waits for a seam
where a widget declares the kind of value it holds; the rule does not name it.

What an untouched field seeds and sends, where that changed. A record form
sends a structured field whole once one of its members changed, so "a sibling
changed" below means an edit that changed another member of the same structured
field, and the row holds for a member the record lacks that is neither
omittable nor presence-required: a member the record holds goes as loaded, and
the backend's form spec makes every member omittable or presence-required, so
no member it emits reaches those rows today.

| Field | Where | Before | Now |
|---|---|---|---|
| a form spec `boolean` | record form: a create; an edit, a sibling changed | `""` | `false` |
| an `array` under the `json` widget | record form: the same two | `{}` | `[]` |
| an untyped value under the `json` widget | record form: a create | `{}` | left out |
| | record form: an edit, a sibling changed | `{}` | `null` |
| a number kind under a widget that is not a number's (`money`) | record form: a create | `""` | left out |
| | record form: an edit, a sibling changed | `""` | `null` |
| a date, a time, or a date and time, carried as text with no declared `format` (a `string` kind with only a date or time widget) | record form: an edit, a sibling changed | `null` | `""` |
| the `date`, `time`, `integer` or `float` widget with no kind | an action dialog's optional argument, at its `submit`; a `MutationDialog` field, at its `parseValues` | `""` | `null` |
| the `datetime` widget with no kind | a `MutationDialog` field, at its `parseValues` | `""` | `null` |
| the `json` widget with no kind | an action dialog's optional argument; a `MutationDialog` field | `""` | `{}` |
| a relation list declared by its widget alone | a `MutationDialog` field, at its `parseValues` | `""` | `[]` |
| a number that is neither required nor nullable | a schema dialog; a `MutationDialog` | `null` | left out |
| an integer under the `select` widget, not required | a schema dialog; a `MutationDialog` | `""` | left out |

A descriptor that declares a kind under a widget of another family follows the
rule above and moves too: an object, a list or an untyped value declared under
a text widget holds `{}`, `[]` or `null` (left out of a create) where it held
`""`, and a text declared under a list or the `json` widget holds `""` where it
held `[]` or `{}`. No other change was found over the pairs a form spec, the
metadata layer or a hand-written descriptor produces.

A field whose empty value is `null` (a number, a date or time, an untyped
value) has no "present but empty" state: holding `null`, or nothing without a
default to use, the presence control shows the field's own control with its
hint ("Left empty", "Optional" or "Not set") and no "Set value". "Set value"
stays for the other fields and writes their empty value; it no longer writes a
`0` nobody chose for a number. Two step configurations of the framework show
it: the extraction steps' `thinking`, holding its `null` default, shows its
control with "Left empty" and "Omit value" where it showed a "Not set" button
beside it; and the start-conversation step's `timeout`, holding nothing, shows
its own control with "Optional" where it showed only "Not set" and a "Set
value" that wrote `null`.

A member that may be left out (it is not `required`) is no longer seeded with a
value its own declared type refuses: it stays absent, in a schema dialog, a
`MutationDialog` and a record form's create draft, through one check. A record
form's edit does not read it yet. The seeding policies otherwise keep their
differences: a schema dialog seeds a `nullable` member with `null` and an
object with its template's seeds, a `MutationDialog` seeds `null` and `{}`, a
record form neither.

Upgrade: an action dialog's `submit` and a `MutationDialog`'s `parseValues` now
receive `null` for an untouched date, time or number field declared by its
widget alone (`date`, `datetime`, `time`, `integer`, `float`), `[]` for a
relation list and `{}` for a `json` one, where they received `""`; code that
tested for the empty text tests for `null`, or for a value. A `submit` that
passed an untouched optional date on as it was now sends `null`, which a date
parameter accepts, where it sent `""`, which it refuses. Upgrade: a test that
pressed "Set value" on a number and expected `0` finds the number's own control
instead.
