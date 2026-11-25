---
"@angee/ui": minor
---

A refusal's form-level messages stay several. A save or a submit refused for
more than one reason showed its reasons as one sentence: the form-level
messages, and each message no rendered field claims, were joined with a space
into one text, on the record form's banner and in both dialogs, and a screen
reader heard one run-on string. The form-level error now holds a list, and
`ErrorBanner` renders it: one message is the banner's body, several are a
list, one item each.

- `ErrorBanner` takes `messages`, a list of strings, in place of `description`.
- `FormViewSaveSurface.saveErrors` replaces `saveError`, and an action form's
  `formErrors` replaces `formError`: each is the list of form-level messages,
  empty when there is none.
- `formErrorsFromForm(errors)` reads that list from a form's errors
  (`root.server`), where `applyFormErrors` stores it; the error's `message`
  is the same messages, one per line.

Upgrade: read `saveErrors` or `formErrors` and pass them to
`<ErrorBanner messages={…} />`; code that tested `saveError` or `formError`
for `null` tests the list's length. A test that matched a refusal's reasons as
one sentence finds one list item per reason.
