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
- `failedWriteOutcome(error)` (`@angee/refine`) says what a failed write's
  error tells: `not-sent`, `unconfirmed`, `refused`, `denied` or `conflict`.
- `DialogForm` takes `pending`, refusing every dismissal (Escape, the backdrop,
  Close, Cancel) while a submission runs: the dialog is `aria-busy`, Close and
  a footer Cancel stay focusable (`aria-disabled`) with their reason as their
  description, and a refused Escape or backdrop press is announced. Given
  `dirty` (true or false) it guards its work from the moment it opens: a
  dismissal of typed values asks first, with the shared unsaved-changes
  question, and a route move asks while it holds typed values or a running
  submission (while a submission runs the question says it is still sending).
  `holdsWork`, read live, lets an owner say it holds nothing, as after an
  accepted submit that moves on. Its `footer` may be a function of
  `{ dismiss, dismissRefusal }`, for a Cancel.
- Every `MutationDialog` and every typed-args `ActionFormDialog` (a record or
  row action with `args`) now behaves so, and neither asks after an accepted
  submit whose `onSubmitted` or `onSucceeded` navigates. An answer that lands
  after the dialog went away is still reported (`useLateAnswerReport`): an
  accepted one toasts its message, a refused one its reasons, a lost one that
  it may not have been sent. A `trigger`-owned `MutationDialog` given no
  trigger while open stays open until it closes.
- The unsaved-changes guard's question says a submission is still sending when
  one runs (`pendingNow`), and the guard says at mount when it renders outside
  the router.
- `MessageActions` shows its actions where the primary pointer is coarse
  (`pointer-coarse:opacity-100`), as touch never hovers.
- `useUnsavedChangesNavigationGuard` takes `leaves(move)`, which says whether a
  route move abandons the form (default: every move), in place of
  `allowSearchChanges`; `pathnameChanges` is the old behaviour.

Upgrade:

- `allowSearchChanges: true` becomes `leaves: pathnameChanges`.
- An open `MutationDialog` or typed-args `ActionFormDialog` renders under the
  app's router and `ModalsHost`, as every app page provides both, and says so as
  it opens when one is missing. `createUiTestProviders` (`@angee/ui/testing`)
  now always provides `ModalsHost`, and `router: true` (or
  `{ initialEntries }`) renders under a memory router kept in `routers`: a test
  that opens a real dialog passes `router`.
- A test that dismissed a dialog mid-submission finds it open; one that
  dismissed it with typed values finds the unsaved-changes question; one that
  read Cancel's `disabled` while it sends finds `aria-disabled="true"`. A
  `DialogForm` footer function takes `{ dismiss, dismissRefusal }`.
- Code that told an envelope refusal from other errors by `instanceof Error`
  alone still works; one that read `response.errors` on a gateway failure finds
  it absent.
