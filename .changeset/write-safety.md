---
"@angee/ui": minor
"@angee/refine": minor
---

A write from a dialog or a composer is safe to retry, and a dialog keeps a
pending write and typed values.

- `useIntentKeys(scope)` (`@angee/ui`, beside `createClientKey`) keeps one
  request identity per write intent for the page's lifetime: `keyFor(intent)`,
  `run(intent, fire)` and `state(intent)`. The intent's object keys may come in
  any order. `run` releases the key on the write's answer (a result, ok or
  refused, a GraphQL error, an in-band envelope refusal) and keeps it when the
  answer is lost, when the connection boundary refused the request before
  sending it, and when the server refused who may write (`state` then says
  `unconfirmed`, `not-sent`, `denied` or `denied-after-unconfirmed`). A surface
  unmounting never releases a key; a change of signed-in person forgets them
  all; nothing is persisted, so a retry writes once from this page only.
- `@angee/refine`: `errorFromAuthoredEnvelope` (and so `useAuthoredMutation`'s
  `errorFrom`) throws an `AuthoredEnvelopeError` carrying the envelope's `code`,
  where it threw a plain `Error`. `hasGraphQLAnswer(error)` says whether a
  failed request got the server's GraphQL answer. `boundedGraphQLTransportError`
  keeps `response.errors` only when the server's response held a GraphQL errors
  list: a network failure, a cancelled request or a gateway's own page carries
  none.
- `DialogForm` takes `pending`, refusing every dismissal (Escape, the backdrop,
  Close) while a submission runs, and `dirty`, asking before a dismissal
  discards typed values with the shared unsaved-changes question. Its `footer`
  may be a function of `dismiss`, the dialog's own dismissal, for a Cancel.
- Every `MutationDialog` and every typed-args `ActionFormDialog` (a record or
  row action with `args`) now behaves so: Escape, the backdrop and Close do
  nothing while it sends, and its answer shows; Cancel, Escape, the backdrop
  and Close ask first when values were typed; the close after an accepted
  submit asks nothing. A `MutationDialog` also asks before a route move while
  it holds typed values or a running submission, and a submission accepted
  after the dialog went away still toasts its message (only `onSubmitted` and
  the close are skipped).
- `MessageActions` shows its actions where the primary pointer is coarse
  (`pointer-coarse:opacity-100`), as touch never hovers.
- `useUnsavedChangesNavigationGuard` takes `leaves(move)`, which says whether a
  route move abandons the form (default: every move), in place of
  `allowSearchChanges`; `pathnameChanges` is the old behaviour.

Upgrade:

- `allowSearchChanges: true` becomes `leaves: pathnameChanges`.
- A dialog holding typed values asks through `ModalsHost`, and a
  `MutationDialog` holding typed values or a running submission guards route
  moves through the router, as every app page provides both. A test that types
  into a real dialog renders it under `ModalsHost`; one that types into or
  submits a real `MutationDialog` renders it under a router too
  (`RouterContextProvider` with a memory history).
- A test that dismissed a dialog mid-submission finds it open; one that
  dismissed it with typed values finds the unsaved-changes question.
- Code that told an envelope refusal from other errors by `instanceof Error`
  alone still works; one that read `response.errors` on a gateway failure finds
  it absent.
