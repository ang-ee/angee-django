---
"@angee/ui": minor
---

An action or mutation dialog that the server refuses says each reason once:
under the field it names when the dialog shows it, on its banner otherwise (a
name only a `content` editor holds shows under the editor and on the banner).
The server joins every reason into the outcome's `message` (a refused target's
message is the verb's summary), and `actionOutcomeSubmitResult` (with
`actionFormSubmitResult`, which composes it) made that message a form-level
reason beside the reasons it joins: a refusal of one field showed the field's
sentence under the field and again on the banner, and the banner took focus.
Now the adapter keeps each field's reasons under its key, moves the non-field
reasons (the `__all__` key) to the form level as they are, and uses the message
only for a refusal that carries no reason at all, as `runActionResult` does; a
refused target shows its reason, not the verb's summary. Focus goes to the
refused field where it can take focus: a text field of a `MutationDialog` or of
a typed-argument `ActionFormDialog`. A schema-driven or definition
`ActionFormDialog` holds its fields in a fieldset it disables while the submit
runs, so a browser cannot focus a native input there and the banner naming the
field takes focus; a checkbox or switch still takes it.

`ActionFormDialog` binds the reasons to the fields it renders for the values the
form holds when the answer lands, so a reason under a hidden field, a field
its `showWhen` hides or a field of an inactive schema
branch shows on the banner, as `key: reason`, instead of being bound to a field
nobody sees. `ActionFormDefinition.fieldNames` is removed and
`jsonSchemaActionArgs` no longer returns it. `UseActionFormOptions.fieldNames`
also takes a function of the form's values, read when the answer lands. A row's
direct submit (`useDescriptorRowActions` with `valuesFromRow`) whose result is
refused for its fields only toasts under the verb's label, not its id (the id
still stands in when the label is not text).

Upgrade: drop `fieldNames` from an `ActionFormDefinition`; nothing replaces it,
and a reason under a name only its `content` editors hold now also shows on the
banner as `key: reason`. A test that reads an adapted refusal whose message sits
beside a field's reason expects no form-level message for it, and one that sends
a non-field reason puts it under `__all__`.
