---
"@angee/ui": minor
---

An `ActionFormDialog` that checks its own rules (its arguments are declared as
`args`, or its definition brings no resolver) shows a failing value's message as
the value changes. Before, it checked nothing before the first submit while its
submit button was disabled as soon as a rule failed, so a value too short or a
required argument typed and then cleared disabled the button with no message on
screen, and a person could not submit to see one. Now the message shows under
its field at the change, while the button is disabled, and goes when the value
is corrected, as in a `MutationDialog`. A value that already breaks a rule when
the dialog opens, a required field left empty or a prefilled text shorter than
its minimum, still disables the button with no message until the person edits
that field. Editing one member of an object field checks the whole field, so it
also shows the messages of the field's other members that break a rule, though
the person has not touched them, while a failing field outside it that the
person has not touched stays silent. A dialog whose definition brings a
resolver, such as the one `jsonSchemaActionArgs` returns, still checks from the
first submit. `useActionForm` takes React Hook Form's `mode` as the new
`UseActionFormOptions.mode`; a caller that passes none still checks from the
first submit.
