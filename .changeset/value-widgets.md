---
"@angee/ui": minor
---

A number shown as a percentage or a progress bar no longer holds the empty text
when it holds nothing. The `percent` and `progressBar` widgets join the number
widgets (`NUMBER_WIDGETS`, beside `integer` and `float`) in the one rule for a
form field's empty value (`emptyValueForField`) and in every reader of that set,
so they behave as `integer` and `float` do: with no `string` kind they hold
`null`, a record form's edit sends one holding the empty text as `null` and a
create leaves it out, and the presence control treats them as numbers. No
control of theirs writes the empty text: `percent` only shows a value, and a
cleared progress bar writes `0`. The rows below hold with or without a bound:
the rule check takes the `null` an optional field is seeded with for the field
holding nothing.

| A field declared by `percent` or `progressBar` with no kind | Where | Before | Now |
|---|---|---|---|
| untouched | its seed: a record form's create draft, a `MutationDialog`, an action dialog's argument | `""` | `null` |
| untouched, on a field its metadata does not know | a record form's create | sent as `""` | left out: it holds `null` |
| holding `""` | a record form's create | sent as `""` | left out: the rule that sends a cleared number as `null` makes it `null` |
| holding `""` (a stored value) | a record form's edit that sends it | `""` | `null` |
| a member the record lacks | a record form's edit that writes its object | `""` | `null` |
| omittable, holding nothing | the presence control | "Not set" and "Set value", which wrote `""` | its own control and "Optional" |
| omittable with a `null` default, holding nothing | the presence control | "Not set" and "Use default" | its own control and "Optional" |
| nullable, holding `null` | the presence control | "Left empty" and "Set value", which wrote `""` | its own control and "Left empty" |
| nullable and not omittable, holding nothing | the presence control | "Not set", "Set value" and "Leave empty" | its own control, "Not set" and "Leave empty" |
| nullable and omittable, holding nothing | the presence control | "Not set", "Set value" and "Leave empty" | its own control, "Optional" and "Leave empty" |

In the presence rows, "its own control" is the progress bar's: a `percent` has
no control (it only shows a value), so only the words and verbs beside it show.

The same holds for a kind that decides nothing (one that names a widget, as
`text` or `select` do) under these two widgets, and for a field whose kind
names one of them with no widget, except the row for a `null` default: the
presence control's compact rule reads the declared widget only, so that field
still shows "Not set" and "Use default". The rows for the number and date widgets are
read before the kinds, so a kind under `percent` or `progressBar` now follows
them as it already did under `integer` and `float`: a `boolean` or `switch`
kind holds `null` where it held `false`, an `object` kind `null` where it held
`{}`, and an `array` kind `null` where it held `[]` (eight cells, the two
widgets by these four kinds); a required one holding `null` is then missing,
and for the `boolean`, `object` and `array` kinds the refusal check
(`refusesSeed`) leaves such a member that may be left out absent. A `string`, `integer`,
`number` or `any` kind keeps its empty value, but its empty text is sent as
these widgets send it: `null` on an edit and left out of a create, where it was
sent as `""`; a `string` kind is left out of an untouched create, and an
omittable `string` or `any` kind with a `null` default shows its own control.
No declared descriptor of the framework or of its products has one of these
pairs, or either widget on a form.

Not changed, and known: a progress bar shows `0` while it holds `null`. A
`percent` declared required cannot be filled, having no control: a dialog's
submit stays disabled for it, where a bounded one submitted `""` before (one
without a bound was already refused). The
`boolean`, `booleanToggle` and `booleanBadge` widgets and the `money` widget
still hold `""` with no kind: a boolean waits for a field's type to reach its
descriptor, where a kind decides it; `money`, which the money addon's fragment
registers, waits for a seam where a widget declares the kind of value it holds,
and the rule does not name it; `EditableLines` keeps its own list of number
widgets for alignment.

Upgrade: a `submit` or a `parseValues` that tested an untouched field declared
by `percent` or `progressBar` alone for the empty text tests for `null`.
