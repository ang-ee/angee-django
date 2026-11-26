---
"@angee/ui": minor
"@angee/refine": minor
"@angee/app": minor
---

A write from a dialog or a composer is safe to retry, and a dialog keeps a
pending write and typed values.

- `useIdempotencyKeys()` (`@angee/ui`, beside `createClientKey`) keeps one
  idempotency key per draft of a keyed write, for the app runtime:
  `send(scope, fire, outcome?)` and `discard(scope)`. A draft is named by its
  scope (the surface and what it writes to); its key is minted at its first
  attempt and lasts until a confirmed answer ends it, or the person discards it
  with nothing possibly written (no attempt lost, none on its way). No error
  releases it, so a resend, edited or not, is written once by the server, which
  replays the same content and answers `CREATION_KEY_CONFLICT` for other
  content under the spent key: the draft's next send then starts a new draft,
  and the caller only says what happened. `useIdempotencyKeyState(scope)` reads
  what the draft can tell (`{ outcome, uncertain }`), re-rendering only its
  reader. The store lives on the root `AppRuntimeProvider`, is forgotten on a
  change of signed-in person (not on entering a preview) and is never
  persisted, so a retry writes once from this page only. The record form's
  create, chatter's post, knowledge's note and vault clone, the project from a
  template, the dashboards' create and duplicate, and the workflow start sit on
  it; chatter and notes read again after a conflict, so the earlier version
  shows. A keyed surface still open when its answer is lost says so, that the
  write may have gone through and that sending again from the page sends it
  once (`formSubmitError`'s `answerLost`; chatter and notes in their own
  words). The record form promises it only where its own create root takes the
  key: a custom create owner (`submit`) receives the draft's key, but its lost
  answer says only that it may have been sent, since nothing tells the form
  the owner sends it. The dashboards' create and duplicate tell a spent key in
  their own words. The relation picker's quick create ("Create “name”") sits on it too
  when its create root takes a key, as one draft per relation field, so a
  retry after a lost answer no longer writes the record twice
  (`useRelationQuickCreate` takes the field's name); it tells a lost answer
  and a spent key in its own words, and any other failure through the data
  layer's notice as before.
- One draft scope belongs to one surface at a time. `useIdempotencyDraftOwner(scope, holder)`
  holds it while the surface is mounted (an open keyed dialog, a composer that
  can post, and every create form, keyed or not), and in development two
  surfaces holding one scope throw at their next render, naming the scope and
  both holders, so two create forms of one resource mounted at once, neither
  nested under the other, fail there; a production build logs it once per
  scope and goes on. A strict-mode remount or a form replacing its predecessor
  in one commit holds it once.
  `useDraftScope(own)` names a surface's scope under the one its host provides
  (`DraftScopeProvider`): the record form provides its own
  (`record.create:<provider>:<resource>`, or `record.edit:…:<id>`), and a
  relation field's dialog its `relation:<field>` slot (`fieldName` on
  `RelationPicker`, `RelationFieldWidget` and `RelationMultiFieldWidget`;
  every form passes it, and a lines editor `<lines>.<row>.<column>`, naming
  the row by its id or client key), so a create form opened from another form,
  down any chain, or from another relation field or row to the same resource,
  never shares its draft.
- `MutationDialog` with `idempotencyScope` (`KeyedMutationDialogProps`) calls
  its submission with the draft's key as a required `string` third argument;
  without one (`MutationDialogProps`, as before) it is called with none.
  `submitMutationDialog` takes the dialog's draft (`MutationDialogDraft`) and a
  keyed one's key. An action takes `idempotencyScope`, which may
  answer `null` when the context names no draft (no record to act on), and its
  `submit` reads `context.idempotencyKey`.
- `FormSubmitResult`'s conflict carries the server's optional `code`
  (`FormSubmitConflictCode`: `STALE_REVISION` or `CREATION_KEY_CONFLICT`), set
  by `formSubmitError` and `actionOutcomeSubmitResult`; a bare conflict stays
  a stale one. `formSubmitOutcome(result)` says what a submit's result tells
  its draft's key, and the record form and both dialogs pass it to `send`, so
  a submission may return its conflict as well as throw it. A lost answer's
  result from `formSubmitError` is marked `outcome: "unconfirmed"`, given its
  sentence or not, so a returned one keeps its draft as a thrown one does,
  past a dismissal. A creation-key
  conflict does not lock a dialog's draft as a stale one does: the next
  attempt is a new draft. `@angee/refine`'s `ActionOutcome` takes a
  refusal's `code`, set by the caller that builds the outcome: the generated
  action documents select none.
- The record form tells a create whose key an earlier attempt spent in its
  own words ("Created earlier": an earlier attempt from this form already
  created a record, to find among the existing ones before saving again), with
  nothing to undo; the save surface's `saveKeyConflict` says it is so, and a
  stale revision keeps its banner and its reload. Both form dialogs bring the
  person to a refusal, as the record form does: `MutationDialog` and the
  typed-args action dialog pass `applyFormErrors` their banner's focus
  (`useRefusalBannerFocus`) and a line naming the refused fields
  (`fieldValidationSummary`); `useActionForm` takes `fieldSummary` and returns
  `banner` to spread on its `ErrorBanner`.
- `@angee/refine`: `errorFromAuthoredEnvelope` (and so `useAuthoredMutation`'s
  `errorFrom`) throws an `AuthoredEnvelopeError` carrying the envelope's `code`,
  where it threw a plain `Error`. `boundedGraphQLTransportError` keeps
  `response.errors` only when the server's response held a GraphQL errors
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
  `allowSearchChanges`; `pathnameChanges` is the old behaviour. The record
  form passes one: a move leaves it when the pathname changes, or any search
  key but the record's tab (`RECORD_TAB_SEARCH_KEY`) does, so a tab switch on
  a form with unsaved edits no longer asks to leave.

Upgrade:

- `allowSearchChanges: true` becomes `leaves: pathnameChanges`.
- A keyed write takes its key from `useIdempotencyKeys().send(scope, fire)`
  under an `AppRuntimeProvider` (`createUiTestProviders` provides one); a key
  minted per attempt (`crypto.randomUUID`, `createClientKey` at send time) or
  held in a ref moves onto it.
- An open `MutationDialog` or typed-args `ActionFormDialog` renders under the
  app's router and `ModalsHost`, as every app page provides both, and says so as
  it opens when one is missing. `createUiTestProviders` (`@angee/ui/testing`)
  and `ShellPageTestProviders` (`@angee/app/testing`) render the same app shell,
  `AppShellTestProviders` (`@angee/ui/testing`), which always provides
  `ModalsHost`. `ShellPageTestProviders` renders under a memory router by
  default, as every shell page renders under the app's router (`{ initialEntries }`
  places it; `onRouter` receives it), and under a router a test or story brings
  around it, it renders that one alone; entries asked for inside such a router
  are refused.
  `createUiTestProviders` renders under one when given `router: true` (or
  `{ initialEntries }`, kept in `routers`): a component test that opens a real
  dialog passes `router`.
- `createMutationDialogTestDouble` sends a keyed dialog's submission its
  draft's key through the dialog's own key owner, one per double: kept through
  refusals and lost answers until an accepted answer or a spent key; it minted
  one per submit.
- A keyed `MutationDialog` submission reads its third argument as a `string`;
  one that asserted it drops the assertion, and one that called `restart`
  after a conflict drops the call: a conflict, thrown or returned with its
  `code`, ends the draft at the owner. Two surfaces holding one
  draft scope at once fail in development: give one its own scope, or nest it
  under its host with `useDraftScope`.
- A test that dismissed a dialog mid-submission finds it open; one that
  dismissed it with typed values finds the unsaved-changes question; one that
  read Cancel's `disabled` while it sends finds `aria-disabled="true"`. A
  `DialogForm` footer function takes `{ dismiss, dismissRefusal }`.
- Code that told an envelope refusal from other errors by `instanceof Error`
  alone still works; one that read `response.errors` on a gateway failure finds
  it absent.
