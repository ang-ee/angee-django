---
"@angee/ui": minor
---

A dialog's refused select, combobox, date, time, tag input or relation picker
takes focus, as a text field of a `MutationDialog` or of a typed-argument
`ActionFormDialog` already did. The binder asked the form library to focus the
first refused field from inside the submit, while the dialog's fields are
locked: a select's or combobox's trigger is disabled, a date, time, tag input
or relation picker shows its read view, and a schema dialog's fields sit in a
disabled fieldset. The call reached a disabled or departed control, focus was
lost, and the banner took it.

- `useRefusalFocus` is now the one owner of where a refusal brings the person.
  `applyFormErrors` calls its `focus` with the first refused field
  (`focus(field)`) or with none for the banner (`focus()`); it no longer passes
  the form library's `shouldFocus`.
- The hook takes the form, `useRefusalFocus(messages, { form, ready })`, waits
  until the surface reports its controls ready, then asks the form's own
  deferred `setFocus` for the field, and gives the banner focus when the field's
  control took none. `MutationDialog` and `useActionForm` pass
  `ready: !isSubmitting`; their fields still lock while they submit. The record
  form, whose fields never lock, passes no `ready`; where its refused save
  brings focus is unchanged. Not asked, the banner takes focus for a list it has
  not handled yet, never again for the same list still showing.
- `formErrorsFromForm` reads one error object as one list, also for a form-level
  error that holds only a message, so the list is new exactly when an answer
  lands.
- `@angee/ui/testing` exports `recordFocusCalls`: every `focus()` call, with
  whether its target could take focus by what the test DOM shows (not in the
  document, disabled, a form control in a disabled fieldset outside its first
  legend, inside `[hidden]`, `[inert]` or a closed `details`, a computed
  `display: none`, `visibility: hidden` or `collapse`, not focusable at all).
  The test DOM
  focuses most of these; a host hidden by an app stylesheet, which tests do not
  load, is a browser's to judge.

Upgrade: nothing beyond what this release's note on `applyFormErrors`'s `focus`
asks; the hook never shipped in another shape. A test that reads
`document.activeElement` right after a refused submit waits for it: the field
takes focus on the form library's timer. A test that read `shouldFocus` on a
`setError` call reads the field `focus` was called with.
