---
"@angee/ui": minor
---

**Changed API.** A record action bound with `useRecordActionMutation` is toasted
by one owner, the action bar. The hook had two paths: by default it projected
the outcome onto the rendered action's contract and the bar toasted it, a
success under its message and a failure under the verb's label; with `settle`
or `linkTo` it handed the outcome to the shared settle owner, which toasted it
under the server's message. The second path and the `settle` option are
removed.

What changes on screen, for the verbs that passed `settle` or `linkTo`:

- a refusal is titled by the verb's label, with the server's reasons (or its
  message) as the body, as every record action projected through
  `runActionResult` already was;
- a transport failure is titled by the verb's label with the error's message as
  the body (it was titled by the error's message);
- an answer that carries no outcome is titled by the verb's label with the
  shared "The action returned no result." as its body; a `noResultTitle` given
  through `settle` is no longer read;
- the success toast appears after the navigation to the created record, the
  refresh and `afterSuccess`, not before them.

And for every verb bound with the hook:

- an answer that carries no outcome is now a danger toast, and the record
  neither refreshes nor runs `afterSuccess`. It was silent and did both; with
  `defaultMessage` it was told as a success;
- `useRecordAction` now applies `defaultMessage` to an empty success message
  too, for every runner; it showed no toast, or an empty-titled one for a verb
  that settled.

Not changed: a record action whose own `run` calls `useActionResultRun`,
`useActionResultMutation` or `useRecordChromeActionMutation` is still toasted
there, under the server's message (the proposal ceremony, round open and
cancel, project lifecycle and creation, task promotion, trash and restore,
account access, message delivery, triage, contributed conditional verbs and
others). A transport failure still also raises the data layer's own error
notification, as before.

`linkTo` is unchanged: a verb that returns the record it created is followed
there, through the same implementation the settle owner uses.

Upgrade: remove `settle: true` and `settle: { … }` from
`useRecordActionMutation` options. An inline `settle` is a type error; one
passed through a variable or a spread still compiles and is ignored. A test
that expected a refusal's toast to be titled by the server's message for such a
verb finds the verb's label.
