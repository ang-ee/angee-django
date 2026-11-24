---
"@angee/ui": minor
---

A value that breaks one of a field's rules now says which, in a record form and
in a dialog without a schema resolver: too short, too long, out of range, too few
or too many items, or the wrong type. Each said "This field is required." before.
A cleared number field that is `omittable`, presence-required or bounded, and not
nullable, now says "Enter a valid whole number." ("Enter a valid number." where
its type is `number`) where it said the field was required; a required number
field with no presence flag or bound still says the field is required. A missing
value still says the field is required, and so does an empty text under a minimum
length of one character in a field that is neither `omittable` nor nullable; in a
field that is either, it says "Enter at least 1 character.". A dialog with a
schema resolver says "Enter at least 1 character." in each case, and for a text
declared nullable with `anyOf` also "Enter a valid empty value. Enter a valid
value.". `@angee/ui/views/validation-errors` exports `jsonSchemaErrorMessage`,
`JsonSchemaRule` and `fieldErrorsFromRules`.
Upgrade: `structuredFieldErrorPaths` is renamed `structuredFieldErrors` and
returns, for each failing path, `{ path, keyword, params }` (`FieldRuleError`)
instead of the bare path; `FormViewSurface.requiredMessage`, which nothing read,
is removed; code that read a form error's `type` as `required` for a length,
range, item-count or type failure reads the rule's keyword instead (`minLength`,
`maximum`, `maxItems`, `type`, …); and a host that embeds `@angee/ui` with its
own i18n instance and its own `ui` translations (an app built with `createApp`
cannot override the `ui` namespace) translates these messages in a singular and
a plural form, which interpolate `{count}` where the old key interpolated
`{limit}`: `form.validation.minLength` becomes `form.validation.minLength_one`
and `form.validation.minLength_other`, `form.validation.maxLength` becomes
`form.validation.maxLength_one` and `form.validation.maxLength_other`,
`form.validation.minItems` becomes `form.validation.minItems_one` and
`form.validation.minItems_other`, and `form.validation.maxItems` becomes
`form.validation.maxItems_one` and `form.validation.maxItems_other`.
