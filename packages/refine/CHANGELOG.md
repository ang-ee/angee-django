# @angee/refine

## 0.2.0

### Minor Changes

- 37387b7: The signed-in user carries `hasUsablePassword` (`AuthUser` in `@angee/app`,
  `RuntimeAuthUser` in `@angee/ui`), so a user-menu item can hide a password
  action for a session without one. The identity query reads it from the user
  type's new `has_usable_password` field, so upgrade the Angee backend with the
  packages. A dialog opened from a contributed user-menu item stays mounted and
  returns focus to the menu's trigger when it closes. A form field may declare
  `autoComplete`, which the password and textarea widgets pass to their input.
  `useActionMutation`, `useActionResultMutation` and
  `useRecordChromeActionMutation` accept `errorNotification: false` and
  `transient`, for a verb carrying a secret input: its mutation state is cleared
  once it settles, and a second call while one runs rejects.
- 2d1043b: A resource field's metadata may carry `createOnly`: the field is a create-only
  implementation selector, which its update input carries and its model's save
  lets change only while the stored key is not registered. A record form reads it
  as the field's `createOnly` (`fieldsWithMetadataDefaults`), so a page no longer
  restates it, and a page's own `createOnly: false` cannot unlock it. `fieldChangeable`
  says whether a person may change a field on a stored record (updatable and not
  create-only); the board's lane drag asks it. A resource's roots may name
  `repairChoices`, its generated repair read `<res>_repair_choices(id, field)`.
  `@angee/refine`'s operation documents gain the `repairChoices` kind
  (`RepairChoicesDocumentMap`), which `angee-web-codegen` derives per resource
  whose roots name the read, as `repairChoicesDocuments` in each schema's
  `actions.ts`, validated against that schema; a root the schema lacks fails
  codegen by its name.
- ffdd6a6: `useAngeeFacets` also returns `errors`: each active facet's own read or decode
  error, or `null`, by facet id; `error` stays the first of them. A hand-built or
  mocked result must include `errors`. A facet's read opens no notice; the search
  panel and the facet shortcut show a refused facet's words above the options its
  last read still holds, and the shortcut's list is described by them.
- 37387b7: `groupByVariables` returns a new named type, `GroupByVariables` (`group_by`,
  `where`, `having`, `order_by`, `limit`, `offset`, `with_rows`, `rows_limit` and
  `rows_order_by`), instead of `Record<string, unknown>`; the values it builds are
  unchanged. A page that hands them to a generated groups document narrows
  `group_by` to its resource's own type at that one member instead of casting the
  whole object. Code that read or added other keys on the result as a plain record
  no longer type-checks.
- 37387b7: `useAuthoredKeysetFeed` takes an optional `subscription` (`{ document,
variables }`): a dedicated GraphQL subscription that refreshes only that feed, in
  place of subscribing to its models' change feeds. Its deliveries are coalesced
  like other live reads, so a feed such as a record's timeline no longer refetches
  on every change of the record's model.
- 37387b7: `List`, `ListView` and `ResourceList` take `relatedModels`, the model labels
  whose changes refresh the list: a change on one of them whose related records
  name any record of the list's model refetches it, including records outside the
  current page that may enter its filter. Use it for columns and filters derived
  from related rows; a list over an authored `source` declares its interests on its
  own query instead. The dialect hooks behind them (`useAngeeAggregate`,
  `useAngeeGroupBy`, `useAngeeGroupByBatch`, `useAngeeListBatch`) take the same
  option, and a list's own and related changes share one coalesced refresh.
- 37387b7: Live changes from other writers reach Refine's resource hooks (`useOne`,
  `useList` under live mode) and authored reads through one coalescing policy: a
  300 ms quiet window with a 2 s maximum wait, so a record written several times a
  second refetches its readers once per burst, with one merged delivery carrying
  every changed id. A read still fetching when a burst flushes is no longer
  cancelled: it commits its answer and refetches as soon as it settles, so a
  continuous stream of changes no longer leaves a slow read on its loading state. A
  caller's own writes still cancel and refetch at once. `AngeeLiveResource` gains an
  optional `identifier`, the registered Refine resource name, which `createApp`
  fills in; a host that builds its own live provider sets it with
  `refineResourceIdentifier(resource)` from `@angee/metadata`.
- 37387b7: `MutationDialog` reports a failure once: a refusal bound to a field stays under
  that field with no fallback banner (`applyFormErrors` sets none while a rendered
  field holds the error), and every call the dialog makes for its caller
  (`validate`, `onSubmit`, `mutation`) receives the new `AuthoredMutateOptions`
  (`@angee/refine`), which turn refine's generic error toast off for that call.
  The dialog takes an authored `mutation` (with an optional `mutationResult`) as
  an alternative to `onSubmit`, so `MutationDialogProps` is now a union of the
  two. An authored mutate function takes the options as its second argument and
  `ActionMutate` as its third; an action's `submit(values, context, options)`,
  `useActionForm`'s `submit` and the dashboard store binding's `save` and
  `createPersonal` receive them as well. `createMutationDialogTestDouble` runs
  `validate` before it submits, as the dialog does. Upgrade: a `validate`,
  `onSubmit`, action `submit` or store binding that makes an authored call passes
  the options on to it (or hands that call to the dialog as `mutation`), so its
  failure is not toasted twice; code reading `onSubmit` from `MutationDialogProps`
  checks for it, since a dialog may carry `mutation` instead.
- 804e3d3: A list read's order reaches the server as an ordered list, so a list ordered by two keys or
  more reads in the priority it shows. A GraphQL server rebuilds an input object in its type's own
  field order, so `order_by: { due: "desc", title: "asc" }` sorted by whichever of the two the
  resource declares first; and the read's key, the list batch hash and a grouped list's scopes
  compare an object with its keys sorted, so `due desc, title asc` and `title asc, due desc` were
  one request, and a pure swap of priority (a saved view, a link, back and forward) read nothing
  again.

  `ResourceQuery.toOrderBy` returns one single-key object per key, in priority order
  (`[{ due: "desc" }, { title: "asc" }]`), and an empty list for no order. `@angee/refine` names
  that shape `ListOrderBy` and takes it where a list's order goes: `listQueryMeta`'s `orderBy`,
  `GroupRowsRequest.orderBy` (`groupByVariables`' `rows_order_by`) and
  `AngeeListBatchScope.orderBy`, which no longer accept an object. A list read's `sorters` go the
  same way, one object per sorter in the order given, a dotted field nested, through
  `@refinedev/hasura` 7.0.1 as Angee's pnpm install policy patches it: the stock `generateSorting`
  merges them into one object. In `@angee/ui` the record pager's captured scope, the navigation
  scope a list publishes and a gantt's bar order follow a pure priority swap.

  **Upgrade:** pass `toOrderBy`'s result on as it is wherever a list's `order_by` goes. A caller
  that wrapped it in a list (`[query.toOrderBy(order)]`) unwraps it, since the server refuses a
  list inside a list; code that read its result as an object reads the list. A project that
  installs through Angee's pnpm policy (`packages/scripts/pnpm-policy.cjs`) gets the
  `@refinedev/hasura` patch; one that does not keeps the stock merge, so a read with several
  `sorters` is ordered by the server's field order, not the sorters' priority.

- 37387b7: `useAuthoredQueryBatch` keeps its Map and each entry's identity until a read its
  callers use changes, so a grouped list no longer rebuilds on every render. Its
  entries are now `QueryBatchEntry` (exported): a readonly `data`,
  `dataUpdatedAt`, `error`, `isFetching` and `refetch`, no longer the whole
  `UseQueryResult`. The new `useDerivedBatch(batch, derive, inputs)` maps a batch
  and reuses each output while its entry is the same object and `inputs` are
  unchanged. A group payload the decoder refuses is now that read's `error` in
  `useAngeeGroupByBatch` and `useAngeeFacets`, as it already was in
  `useAngeeGroupBy`, instead of throwing in render, and in all three it is
  reported once per settle as a failed read is (refine's auth check and error
  notification). Upgrade: read any other query property (`status`, `isStale`)
  from your own `useQuery`.
- e7839e0: `@tanstack/react-query` must be at least 5.102.0, the release that adds
  `queryClient.query` and `infiniteQuery`. The app's route identity gate, auth
  provider and view-as identity read use them in place of the deprecated
  `ensureQueryData` and `fetchQuery`, so a host on an earlier 5.x release must
  upgrade it.
- 37387b7: A new entry point, `@angee/refine/testing/wire`, exports `graphqlWireFetch`, a
  GraphQL wire fake for stories and tests: a `fetch` for the Angee GraphQL
  transport that answers each root field of a request from the handler named for
  it and answers the session's CSRF probe. A root field without a handler, an
  unparseable document or a missing required variable fails loudly; a handler that
  throws `GraphQLError` (re-exported there) answers with that error, and an
  optional `onRequest` observes each parsed request. It imports no test runner,
  so Storybook and Vitest can both use it; its request and handler types are
  `GraphQLWireRequest` and `GraphQLWireOperation`.
- 2bea088: A record action the server refuses tells the person why. When a verb's target is
  refused before the verb runs (not found, not allowed), the server answers with the
  verb's summary as the message ("Confirm failed.") and the reason in-band, in
  `validation_errors`. `runActionResult`, which projects an action's outcome onto
  the rendered action's contract, threw only the message, so the action bar's toast
  read "Confirm / Confirm failed." and the reason was lost. It now throws the
  reasons, and the message when the refusal carries none. Every record action
  projected through `runActionResult` now shows the in-band reason without an
  edit. No toast title changes. Other refusals (a transition not allowed, a
  missing object) still read the verb's summary: the server sends no reason for
  them.

  `@angee/refine` exports `actionOutcomeReasons(outcome)`: a refusal's in-band
  reasons, under any key, each once, as one line. `useActionResultRun` composes it
  instead of its own copy of the rule, and so does a refused row of a bulk trash
  or restore, which showed only the first reason.

  Upgrade: code that matched the thrown error's text against the outcome's
  `message` finds the reasons there when the refusal carries any.

- 37387b7: A payload that a collection source's `select` refuses no longer throws in
  render: it becomes that read's error and is reported once per settle of the
  read, as a failed read is. `@angee/refine` exports `useReportRefusedReads`, the
  hook that does this reporting, beside `useDerivedBatch`; its facets and group
  reads already use it.
- 37387b7: A list can let a person select everything its read matches: `ListView` and
  `List` take `selectAllMatching`, which offers "Select all N" where the pager
  counts what a verb acts on, and a page's `bulkActions` then receives
  `collection.allMatching` (`AllMatching`, `AllMatchingRecords` with the list's
  `where`, or `AllMatchingGroups` with the groups read, its order included).
  Opting in asserts that every page verb honours that mode or refuses it; the
  built-in Archive, Trash, Restore, Delete and Share refuse while it is on.
  `Checkbox` takes a `disabledReason`; from
  `@angee/ui/views/resource-view-list-body`, `SelectionToggle` takes a
  `disabledReason` and `ListSelectionBar` takes `matching`
  (`ListSelectionMatching`) and `deleteDisabledReason`. `AngeeListBatchEntry`
  from `@angee/refine` gains a required `updatedAt`, when the page last settled.
  Upgrade: code that builds an `AngeeListBatchEntry` by hand (a test double, say)
  sets `updatedAt`, `null` until the page has settled.
- 37387b7: An app built with `createApp` now recovers from a server outage on its own. A
  network failure or a 502, 503 or 504 answer keeps that request's ordinary error
  and starts one recovery check; only a failed check opens a connection dialog and
  stops further requests, and recovery refreshes the current route and the reads
  the outage refused. Failed mutations are never replayed: a write refused during
  the outage is reported once the server is back.

  `@angee/refine` exports `ServerConnection`, `ServerUnavailableError` and
  `ServerConnectionStatus`, and `createAngeeGraphQLClient` takes a `connection`
  option, the gate one app's schema clients share; `retryableQueryError` no longer
  retries a `ServerUnavailableError`. An addon replaces the dialog's artwork or adds
  guidance through the new `connection.lost#artwork` and `connection.lost#help`
  containers. `@angee/ui` exports `AnimatedThemeLogo`, which the dialog and the
  sign-in page use, and carries the dialog's `connection.*` copy. A host needs no
  change.

- 7214202: A write from a dialog or a composer is safe to retry, and a dialog keeps a
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
    (`useRefusalFocus`) and a line naming the refused fields
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
    after the dialog went away is still reported (`useLateAnswerReport`, now
    exported with its `LateAnswer` type, for any surface whose answer can land
    after it is gone): an accepted one toasts its message, a refused one its
    reasons, a lost one that it may not have been sent. A `trigger`-owned `MutationDialog` given no
    trigger while open stays open until it closes.
  - The unsaved-changes guard's question says a submission is still sending when
    one runs (`pendingNow`), and the guard throws at render outside the router
    (Upgrade, below).
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
  - **Changed contract:** every surface the unsaved-changes guard protects
    renders under the app's router and `ModalsHost`, as every app page provides
    both: a `DialogForm` given `dirty`, from the moment it opens, so every open
    `MutationDialog` and typed-args `ActionFormDialog`; the record form
    `FormView`, editable or not, with the create and edit dialogs a relation
    field opens; and a page guarding its own edits (the dashboard surface, the
    appearance settings page, the workflow studio). Rendered without the router
    such a surface throws at render
    `useUnsavedChangesNavigationGuard must be used under the app's router.`, and
    without `ModalsHost`, `useConfirm must be used under ModalsHost.`. A test or a
    story provides both through the shared providers: `createUiTestProviders`
    (`@angee/ui/testing`) and `ShellPageTestProviders` (`@angee/app/testing`)
    render the same app shell, `AppShellTestProviders` (`@angee/ui/testing/shell`),
    which always provides `ModalsHost`. `ShellPageTestProviders` renders under a
    memory router by default, as every shell page renders under the app's router
    (`{ initialEntries }` places it; `onRouter` receives it), and under a router a
    test or story brings around it, it renders that one alone; entries asked for
    inside such a router are refused. A story renders under the workshop's routed
    fixtures (its decorator, `RoutedRuntimeFixture`, `ComposedRuntimeFixture`).
    `createUiTestProviders` renders under one when given `router: true` (or
    `{ initialEntries }`, kept in `routers`): a component test that opens a real
    dialog passes `router`.
  - A new entry point, `@angee/ui/testing/shell`, exports `AppShellTestProviders`
    and `AppShellTestProvidersProps`. It imports no test runner, so a story
    bundles it as a test does, and `@angee/app/testing` takes the shell from it,
    so that entry reaches no test runner either: a story may import its
    `ShellPageTestProviders` and `ChatterTabsTestHost`. `@angee/ui/testing` keeps
    `createUiTestProviders`, whose spied data providers need Vitest, and a story
    never imports it.
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

### Patch Changes

- 54cdc10: A write's refresh no longer leaves an unanswered first read idle on its way to
  being replaced. `invalidateAuthoredQueries` cancels a read of a moved model that
  has not answered yet, so that a request sent before the write cannot commit
  after it, and, if the read is active, sends it again. It awaited the
  cancellation before the resend, and between the two the read was neither
  answered nor on its way: `isFetching` and `isLoading` were false with no data. A
  reader that waits for a first answer takes that for an answer that will not
  come.

  A record form's confirmation used to come after this refresh; since it confirms
  on the write, it renders in that gap.

  The cancellation and the resend now happen in one synchronous call. The
  cancellation rejects the request synchronously, so the resend is a new request,
  and reverts the read synchronously, so nothing overwrites the resend's state;
  the resend's answer is the one committed. `useSetAuthoredQueryData`, which
  cancelled and awaited the same way before accepting a result, is changed the
  same way; and when its updater accepts nothing it no longer cancels the read,
  which it used to leave idle with no data and nothing to resend it.

  What else changes: two writes whose refreshes start within a few microtasks of
  each other during one unanswered first read each restart it (three requests,
  where the awaited cancellation sent two), as writes further apart already did,
  and the first caller's promise resolves when its own resend is replaced.

- 37387b7: Angee's live provider delivers a change on a change feed that several resources
  share under each of their models. A multi-table child model without a feed of
  its own reads its nearest ancestor's feed, so a change published there now
  refreshes the child's readers as well.
- 616e025: A caller's write cancels a refetch that no enabled observer watches.
  `invalidateAuthoredQueries` cancelled only reads with no data, and refetched only
  active reads. A read that had data and was refetching when its last observer
  left (the person navigated away; the transport does not abort), or whose
  observers are all disabled, was neither cancelled nor refetched: its request,
  sent before the write, committed its answer afterwards and cleared the invalid
  mark, so returning within the stale time showed the answer from before the
  write, with no refetch.

  That refetch is now cancelled with the others. The read keeps the data it had,
  stays invalid, and refetches when an enabled observer mounts, as an inactive
  read that was not fetching already did. For a read that fetches only on demand
  (a disabled observer whose person asked for a refresh), the refresh asked for
  before the write is dropped and the read shows what it held, marked invalid;
  before, it committed the answer from before the write, which missed the write
  as well.

  A refetch an enabled observer watches is replaced as before, and whoever awaits
  it still receives the resend's answer.
