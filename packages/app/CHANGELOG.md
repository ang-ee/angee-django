# @angee/app

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
- 37387b7: Over several apps, the app chooser and the expanded rail's caption show a
  declared brand's mark and name (the deployment's, the selected app's or its
  menu root's), and keep the framework mark and "Apps" otherwise; the first rail
  root's label and icon, which stand in for a missing brand elsewhere, are not
  shown there. An unregistered mark falls back to the framework glyph.
  `useDeclaredBrand()` returns the declared brand, or null, and
  `AppRuntime.brandFallback` (set by `createApp`, and on `appSelection`'s result)
  is `true` when the runtime brand is such a stand-in.
- 2f8f65c: The app's rule for retrying a failed read has a name: `retryAppQuery`, exported
  from `@angee/app/create-app` (three retries, for a failure a retry can fix; a
  public GraphQL answer such as a refusal is not retried). The app's query client
  reads it as before. A surface that builds its own query client passes it so its
  reads fail as the app's do.

  The workshop's shared runtime fixture now does: a story about a refused read
  showed its error late (the data library's default retried the refusal three
  times, with a growing pause between tries). Only the retry rule is shared: the
  fixture's other query defaults (how long a read stays fresh, how long it is
  kept) are still the data library's, not the app's. The delay and its cure were
  read from the code and pinned by a test of the fixture's client; neither was
  timed in a browser.

- 37387b7: An app may lock its theme: an app root's menu entry or an `ANGEE_UI.shell.apps`
  entry sets `themeLocked: true` beside its `theme` (`createApp` refuses a lock
  without one; `ANGEE_UI.shell` itself takes none). Within that app the locked
  theme and the host's options replace the person's, while their colour scheme
  still applies; their saved theme is kept for other apps, and the first-paint
  cache keeps their own theme, options and scheme. `AppearanceProvider` takes a
  `themeLocked` prop and `AppearanceState` exposes `themeLocked`.
  `RadioGroupItem` now puts `aria-describedby` on the focusable radio instead of
  its label.
- 37387b7: Cards are one contract on the gallery and the board. A card renderer is
  `renderCard(row, context)` (`CardRenderer`), where `CardRenderContext` is a
  union on `kind`: a gallery card gets `selected`, a board card `selected`,
  `dragging` and `laneId`. It is called as a plain function, so it holds no hooks.
  `ListView` and `ResourceList` offer a built-in `gallery` view kind where the
  list has a card to draw: the page's `renderCard`, its `gallery` card fields, or
  the model's card, registered as the one child of `<app.Model>#card` (content
  `{ render, frame? }`, the fields it reads in `requiredFields`), which draws that
  model's board and gallery wherever a page declares no card; `createApp` refuses
  a card on `resource#card` or one reading unknown fields. Cards share one frame
  recipe (`cardFrameVariants`, `inset` or `flush` through
  `ListViewProps.cardFrame`, and `CardSelection`), and board cards show and toggle
  selection. `RowsListView`'s grid is the `gallery` view kind, in the URL as
  `?view=gallery`. An addon container may declare `unique: "address"`, at most
  one child per rendered address. Upgrade: `GalleryView` takes `getRowId` in place
  of `rowKey`, and takes its title from `titleField` or the new `cardLabel`
  instead of a `title` or `name` field; `ResourceViewSwitcher` no longer takes
  `mode` (`"layout"` included) or a type parameter, and `ResourceToolbar` no
  longer takes a `viewSwitcher` node (`chrome.viewSwitcher` stays), so a page
  offers its kinds through the collection's views.
- 37387b7: `createApp` takes an optional `history`, the TanStack Router history the app
  navigates with (such as `createMemoryHistory()`), so a workshop or an embedded
  host can keep the app's navigation off the browser's address bar.
  `REFINE_CREATE_ID`, the record id a create route uses, is now also exported
  from `@angee/ui/runtime`.
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
- d773a02: The keyboard focus ring is Tailwind's ring layer. `focus-ring` and
  `focus-ring-danger` write Tailwind's ring variable instead of a `box-shadow`
  of their own, so the one `box-shadow` Tailwind composes carries the ring and
  the element's own `shadow-*` elevation side by side.

  What a keyboard user sees:

  - In forced-colours mode (Windows High Contrast), every control that draws the
    ring shows a 2px outline in the system's colour, 2px outside it. The browser
    drops every `box-shadow` in that mode; in the last release no control that
    draws the ring showed focus there (controls with their own outline or border,
    such as the calendar's buttons, did). The outline is declared for forced
    colours only: normal mode paints no outline.
  - A focused control keeps its elevation shadow beside the ring: the active pill
    tab, the pressed segmented toggle, the drawer rail's tabs, a toast, the
    block-card link, an interactive card and a board or gallery card's frame.
  - An open tab panel, which Base UI puts in the tab order, shows the ring when
    the keyboard focuses it.
  - In the form's editable fields, a focused input, textarea or button directly
    inside the field drops its own ring for the field's; a control that draws a
    focus-within ring on an element of its own (number, search, JSON, markdown,
    tags) keeps it.

  The ring's colour, width and offset are the theme's `--ring` and
  `--ring-danger`, unchanged in name and value, whatever elevation a theme or a
  person chooses.

  An elevation token is a shadow, never `none`. A `shadow-*` utility composes
  `--elevation-*` into the same `box-shadow` as the ring, and a layer a browser
  cannot draw voids the whole list, ring included. "No elevation" is written
  `0 0 #0000` (the Ledger theme and the flat elevation choice now do). The theme
  contract holds every elevation, `--ring` and `--ring-danger` to a list of
  shadows in fixed forms, wherever a token layer is checked (a theme definition,
  its resolved options and a document's appearance): per layer, an optional
  `inset`, two to four lengths in `0`, `px`, `rem` or `em`, and an optional hex,
  `transparent`, `currentcolor`, `rgb()`/`rgba()`, `rgb(from … r g b / a)` or
  `color-mix(in …, … %, transparent)` colour. Anything else is refused, `none`,
  `0`, a named colour, `calc()` and other units included. `isShadowList` exports
  that judge and `THEME_ELEVATION_PRESETS` the elevation choices. A theme of
  yours that writes another value changes it to one of the forms.

  `@angee/app`: the composer refuses a theme stylesheet that declares an
  `--elevation-*` property, names one in `@property`, or holds a backslash, reading the text with its comments and strings, and names the
  theme; a theme sets its elevations in its definition, where the contract checks
  them. Other custom properties a theme stylesheet declares are untouched. An
  elevation set outside a theme (an addon's own stylesheet, the host's CSS, an
  inline style) is not checked. `assertThemeStylesheet`
  (`@angee/app/web-codegen`) is that check.

  Code of yours that removes a ring writes `ring-0`, and `outline-none` for
  forced colours, under a selector that outranks the ring's state variant:
  `shadow-none` clears an element's own shadow, never the ring, which is a layer
  of its own. An elevation written as a raw `box-shadow` (an arbitrary
  `[box-shadow:…]`, an inline style) competes with the ring: spell it with a
  `shadow-*` utility to keep both.

  The outline needs the `forced-colors` media query: a browser without
  `forced-colors` support shows no indicator in that mode.

- a6093ed: An addon may supply the deployment's appearance at run time through the new
  `AddonManifest.hostAppearance` key, a `HostAppearanceSource`. Like
  `dashboardStore` it is install-one: two installed providers fail composition
  with `Addons "a", "b" each provide the host appearance; install one.`, and
  `ComposedAddons.hostAppearance` holds the one source or `null`. `createApp`
  calls the source's `useOptions(theme, build)` hook once per render of its
  session frame, above every layout, and passes its `HostAppearanceRead`
  (`{ options, pending }`) to the appearance provider; where those options rank is
  the appearance guide's "Host and user precedence". `HostAppearanceDefaults`
  gains `pending`: while it is set, `AppearanceProvider` keeps each person's first
  paint, and the boot cache, where they wear the host's options (a person following
  the app default, or a locked app); a person's own saved theme or customisation is
  painted at once, and after the first paint every choice applies at once, a
  draft's included. Identity's own hold is unchanged. Only the session frame sets
  it: `CreateAppInput.appearance` is now `Omit<HostAppearanceDefaults, "pending">`.
  `AppearanceState.hostOptions` keeps its identity across renders that change
  nothing. A host with no source installed is unchanged.
- 37387b7: A theme or branding fragment can own the sign-in presentation by contributing
  one child to the new `auth.login#hero` container. Its content is a
  `LoginHeroContent` (`@angee/ui/runtime`): a `render` component, an optional
  `presentation: "full-bleed"` and an optional `backgroundImageUrl`. `LoginPage`
  renders it on every page that composes it unless an explicit `hero` prop is
  passed; a second contributor, or a child without a `render` component, fails at
  boot. A host's hero (the prop or a contribution) now renders at every width,
  stacked above the sign-in card on small screens; the framework's own hero still
  shows from `lg` up. `LoginPage` also takes `presentation="full-bleed"`, and its
  `presentation` and `backgroundImageUrl` serve only its own hero: a contributed
  hero brings its own. The sign-in URL is the declared path of the `auth.login`
  route, which the auth gates, the auth provider and `useLoginPath` read.
  Upgrade: remove `loginPath` from `createApp`'s input and declare the
  `auth.login` route at that path instead.
- b74c247: `defineAngeeWebViteConfig` writes a brotli and a gzip sidecar (`<file>.br` at
  quality 11, `<file>.gz` at level 9) beside every `.js`, `.mjs`, `.css`,
  `.html`, `.svg` and `.json` file a build emits that is 1 kB or larger, for an
  ingress that serves a precompressed file. A sidecar that is not smaller than its
  source is not written; fonts and images get none; the output is the same bytes
  for the same input. The files go through a pool sized from the machine's
  parallelism, at most four at once (zlib's work runs on libuv's four threads),
  so a build with hundreds of assets holds no more in memory at a time than a
  small one. The new option `precompressConcurrency` sets the pool's size; the
  sidecars are the same bytes at any size. A host needs no change.
- e7839e0: `@tanstack/react-query` must be at least 5.102.0, the release that adds
  `queryClient.query` and `infiniteQuery`. The app's route identity gate, auth
  provider and view-as identity read use them in place of the deprecated
  `ensureQueryData` and `fetchQuery`, so a host on an earlier 5.x release must
  upgrade it.
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

- 37387b7: `readSharedIdentity(queryClient, authProvider)` (`@angee/app/providers/auth`)
  reads the shared identity entry and reuses its last answer, as the route gate and
  the auth provider's `check` and `getPermissions` do. `ActionFormDialog` enforces
  the required fields of an action's form definition, static or computed from the
  values: a missing field shows its error and the submit button stays disabled
  until it is filled, while a definition with its own resolver keeps its own value
  checks.
- 37387b7: An `ANGEE_UI.shell.apps` entry may omit `rail`: such an app shows every menu
  root with its own brand and theme, else the shell's, so `apps: {console: {}}`
  makes `?app=console` the full console on any host (such an entry may not set
  `home`; the boot check refuses one). The new `ANGEE_UI.shell.app` names the app
  a page opens when neither `?app=` nor the hostname's `hosts` entry selects one,
  and is checked at boot like `hosts`. A `RuntimeSelection` (`@angee/ui/runtime`)
  may therefore name an `app` while its `rail` is `null`, which the developer
  summary shows as "(every root)".
- 37387b7: `useSignInRedirect(redirectTo?)` (`@angee/app/auth`) returns the navigation
  after a sign-in (the page's safe `next`, else the safe `redirectTo`, else `/`,
  as a full page load), which `LoginPage` uses and a custom sign-in route can
  share. `safeRedirectPath` resolves a `next` value the way the browser will and
  accepts only a same-origin result, returning its resolved path, query and
  fragment, so values such as `/<tab>/host` no longer escape to another site.
  Browser error reports leave URL fragments out of the page address, navigation
  and request breadcrumbs, and stack frame locations.
- e7fe5c9: An addon web package can declare a standalone page in `web/package.json`
  `angee.pages`: an HTML entry, the URL paths it answers and the media type a
  request asks for it with. `defineAngeeWebViteConfig` builds each declared page
  as `<id>.html` beside `index.html`, serves it in development at its paths to
  a `GET` or `HEAD` whose `Accept` header matches the manifest's `pageAccept`
  pattern, and serves the built file the same way in `vite preview` (the build
  describes its pages in `.vite/angee-pages.json` inside the distribution).
  `generateWebRuntime` returns the resolved pages and the manifest's `pageCsp`,
  and fails when a declared entry is missing; the build fails when a declared
  page is not emitted, or when its document references `../` or uses `srcset`.
  A built page's document also preloads the modules its entry imports
  dynamically (`<link rel="modulepreload">`), which Vite does only for static
  imports.

  A page's document is given the design tokens and the installed themes as
  stylesheets (`@angee/ui/tokens.css`, a new export of the token sheet alone, and
  `runtime/web/themes.css`), not the host's own stylesheet. It carries no content
  security policy of its own: the ingress sends the manifest's `pageCsp` as a
  header, preview does the same, and development writes it into the document
  after Vite's own head scripts. A manifest that declares a page but publishes no
  `pageCsp` (or an empty one) fails `generateWebRuntime`, and with it the build
  and the dev server. A host needs no change.

  `@angee/ui/theme-runtime` exports `resolveAppearance`, the one resolution of
  what a document wears from the build's appearance payload (the host's theme,
  its forced or system color scheme and its token layers, or a named installed
  theme), which `bootstrapAppearance` now uses, and `readAppearancePayload` for a
  page, which is given that payload as a JSON data block in place of the inline
  first-paint script.

- 37387b7: A deployment declares its static page head in `ANGEE_UI.shell.site` (`title`,
  `description`, `icon`, `touchIcon`, `image`; the type is `ShellSettings.site`),
  whose files name assets an addon declares in its `web/package.json`
  `angee.assets`. `defineAngeeWebViteConfig` emits those files and writes the
  title, description, Open Graph, `twitter:card` and icon tags; absolute preview
  URLs come from the `ANGEE_PUBLIC_ORIGIN` environment variable, which must be a
  bare http(s) origin or the build and the dev server fail. A deployment with
  `shell.brand` and no `shell.site` now gets the brand's name as its `<title>`,
  and `generateWebRuntime` returns the resolved head as `head` (`SiteHead`, with
  `shellSiteHead`, from `@angee/app/shell-runtime`). Upgrade: a host that declares
  `shell.site` removes from its `index.html` every Open Graph and `twitter:card`
  tag, and the description and icon tags the site writes, since the build and the
  dev server refuse a second owner of one.
- d18d3ae: A customizable theme may decline the run-time host appearance source:
  `createThemeCustomizationOptions(defaults, { version, acceptsHostAppearanceSource: false })`
  sets the new optional `ThemeOptionsDefinition.acceptsHostAppearanceSource`
  (default true), and `defineTheme` refuses a value that is not a boolean. While the
  host theme declines, `createApp` asks the `hostAppearance` source about no theme and
  no build options; whenever it asks about no theme, or about a theme without options,
  it wears none of the answer, options or `pending`, so the build's options for its own
  theme apply whole. The flag is not part of the options envelope or of the theme
  catalogue: no options version changes, and saved options resolve to the same tokens.
- 37387b7: The shared Vitest defaults (`@angee/app/vitest`) unmount every DOM test after
  it runs, as Testing Library does under Vitest globals, and run test files in
  worker threads instead of forked processes. A file's own `afterEach` hooks run
  before the unmount. Upgrade: a suite's own `afterEach(cleanup)` is now
  redundant and can go; a hook that must tear down after the unmount calls
  `cleanup()` itself, and a suite that needs a process-only API (such as
  `process.chdir`) sets `pool: "forks"` in its own config.
- 37387b7: The shared Vitest configs (`defineAngeePackageVitestConfig` and
  `defineAngeeWebVitestConfig` from `@angee/app/vitest`) run zone suites under
  pinned process time zones. A package with a `*.zones.test.ts` or
  `*.zones.test.tsx` file runs as three projects: `default` (every suite, in the
  runner's zone) and `TZ=Asia/Tokyo` and `TZ=America/Los_Angeles` (the zone suites
  again, in forked processes). A package with no zone suite keeps its single
  project.
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

- 37387b7: `LoginPage` shows the "or use password" divider only beside a sign-in method that
  renders. A contributed method that renders nothing, such as a single sign-on
  method with no configured connection, no longer leaves an orphan divider above
  the password form.
- 37387b7: The app `createApp` builds turns off Refine's telemetry (`disableTelemetry`), so
  a page load no longer sends a request to `telemetry.refine.dev`. A host needs no
  change.
- 37387b7: Time-of-day fields (the GraphQL `Time` scalar, `HH:MM[:SS]` with no zone) can
  be shown and edited. `@angee/metadata` gives a `Time` field the new `time`
  widget and filter field type (`ResourceFilterFieldType` gains `"time"`), and
  its client-side filtering and sorting compare clocks as SQL does (`09:30`
  equals `09:30:00`). `@angee/ui` registers the `time` widget, which edits
  through the browser's time control and stores a cleared time as `null`;
  `formatTime` and `formatTimeInput` read a `Time` value as written, with no
  zone; the filter editor offers a `time` field type. The generated web types map
  `Time` to `string`.
- Updated dependencies [37387b7]
- Updated dependencies [ae89168]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [ab02c11]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [8d666f2]
- Updated dependencies [37387b7]
- Updated dependencies [2be0fbd]
- Updated dependencies [2d1043b]
- Updated dependencies [69b3f6a]
- Updated dependencies [37387b7]
- Updated dependencies [8e4f4e1]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [30afed5]
- Updated dependencies [f3edb91]
- Updated dependencies [37387b7]
- Updated dependencies [5073340]
- Updated dependencies [37387b7]
- Updated dependencies [ffdd6a6]
- Updated dependencies [1f3a8b4]
- Updated dependencies [f039872]
- Updated dependencies [54cdc10]
- Updated dependencies [d773a02]
- Updated dependencies [30b173f]
- Updated dependencies [3ad391f]
- Updated dependencies [63b200c]
- Updated dependencies [37387b7]
- Updated dependencies [39bec0a]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [39bec0a]
- Updated dependencies [bc9d550]
- Updated dependencies [a6093ed]
- Updated dependencies [42e57b0]
- Updated dependencies [075e9bb]
- Updated dependencies [37387b7]
- Updated dependencies [f3713fd]
- Updated dependencies [58eabd4]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [d42d134]
- Updated dependencies [37387b7]
- Updated dependencies [2cc25ca]
- Updated dependencies [37387b7]
- Updated dependencies [50f0cb4]
- Updated dependencies [640db7c]
- Updated dependencies [37387b7]
- Updated dependencies [2881e0d]
- Updated dependencies [804e3d3]
- Updated dependencies [988b712]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [975d1f9]
- Updated dependencies [c55f0b8]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [e7839e0]
- Updated dependencies [2f99820]
- Updated dependencies [f97b73c]
- Updated dependencies [56fd4d4]
- Updated dependencies [37387b7]
- Updated dependencies [c163be9]
- Updated dependencies [37387b7]
- Updated dependencies [2c3e5b7]
- Updated dependencies [37387b7]
- Updated dependencies [90e0f2b]
- Updated dependencies [802559c]
- Updated dependencies [37387b7]
- Updated dependencies [4bd5849]
- Updated dependencies [836268c]
- Updated dependencies [2bea088]
- Updated dependencies [499bc0e]
- Updated dependencies [37387b7]
- Updated dependencies [7aac88f]
- Updated dependencies [e3bf740]
- Updated dependencies [4aed6db]
- Updated dependencies [0b30a23]
- Updated dependencies [37387b7]
- Updated dependencies [4032bc5]
- Updated dependencies [8b9f4fe]
- Updated dependencies [89ea137]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [be22b32]
- Updated dependencies [e3cb0a0]
- Updated dependencies [d2a5fe7]
- Updated dependencies [e7fe5c9]
- Updated dependencies [58eabd4]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [d18d3ae]
- Updated dependencies [8c594e0]
- Updated dependencies [37387b7]
- Updated dependencies [6722b98]
- Updated dependencies [946895d]
- Updated dependencies [616e025]
- Updated dependencies [e2df484]
- Updated dependencies [18ee144]
- Updated dependencies [7214202]
  - @angee/ui@0.2.0
  - @angee/refine@0.2.0
  - @angee/metadata@0.2.0
