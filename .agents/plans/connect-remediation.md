# Connect remediation — audit-driven fix plan

Consolidated from four independent reviews (architecture + django + react on
`messaging`; a full diff-vs-main audit on `parties`/`parties_integrate_carddav`/
`integrate`/UX). Every item is placed at its **owning level** with the v0 or
framework standard it must meet. No quick fixes — each is technical investment.

Provenance is clean across the diff except item **M10**. `main`-ref note: the
local `main` is stale, so `git diff main` shows `packages/base` widget churn that
is already on `origin/main` (merge noise) — NOT this work; ignore it.

Base state (committed): the AGENTS.md "never quick-fix / technical investment"
rule, the schema fixes that restored `angee dev` (`.build()` on the aggregate
builders, unique enum names, `name_prefix` on the aggregates), and this plan are
committed as the clean, running base. The remediation proceeds from there; M1–M5
are the still-outstanding blocking runtime bugs.

## BLOCKING — committed messaging M1 fails at runtime (23e69a64 / 5b053856)

- **M1. Counter `update()` crashes + corrupts.** `messaging/managers.py:244-249`
  writes `updated_at` (NOT NULL `auto_now`) via `.update()` with `parsed.sent_at`
  → NULL → `IntegrityError` on the first `sent_at=None` message; and
  `last_message_at` is a blind overwrite that regresses backward on out-of-order
  ingest. Fix: drop `updated_at` from the dict (let `auto_now` own it); make
  `last_message_at` monotonic via `Greatest(Coalesce(F(...), sent_at), sent_at)`
  guarded on `sent_at`. The module docstring claims "F() never read-modify-write"
  — honor it. Add the regression test (M7).
- **M2. Attachments silently, permanently dropped.** `managers.py:281-292`
  `_ingest_file` probes `getattr(File.objects, "ingest_bytes")`, which never
  exists — storage's real verb chain is `FileManager.draft()` →
  `File.receive_bytes()` → `File.finalize()` (`storage/models.py`). Every
  attachment becomes a `Part` with `file=None`, bytes discarded, no log.
  **DECIDED: implement.** Wire the real storage owner chain —
  `FileManager.draft()` → `File.receive_bytes(body)` → `File.finalize()` (add a
  `File.objects.ingest_bytes(...)` convenience verb at the storage owner if that
  reads cleaner) — under the channel-owner context, so attachment bytes persist
  and `Part.file` resolves. Remove the dead `getattr` probe.
- **M3. The one editable write path is broken.** Bare `<Field name="status"/>`
  (`MessagesPage.tsx:66`) and `visibility` (`ThreadsPage.tsx:57`) submit UPPERCASE
  enum member names; the lowercase-keyed `*Patch` String inputs reject them (the
  documented frontend enum pitfall). Fix: `<Action set={{status: "..."}}>` verbs,
  or `options` with lowercased values + `createOnly`. Mirror DirectoriesPage
  (readOnly) / agents.
- **M4. Thread Messages tab never loads.** `ThreadsPage.tsx:20` filters
  `{thread:{exact:recordId}}`; the SDL relation lookup is `sqid`. Fix to
  `{thread:{sqid:recordId}}` and handle `fetching`/`error` (currently any failure
  renders as the empty state).
- **M5. `sender` axis is dead — the messaging half of "can't see handles".**
  `MessageType` does not expose `sender`, so the sender facet, group option, and
  `sender.value` column silently render nothing. Fix: expose
  `sender: HandleType | None` on `MessageType` in `schema.py`, regen SDL; the
  facet/column then resolve. (The inbox must show who a message is from.)

## SHOULD-FIX — messaging

- **M6. Keep the social-half scaffolding (DECIDED: keep — social is next).**
  `Reaction`, `MessageMetrics`, the extra `EdgeKind` members, `Thread.parent/body/
  tags/subject_url`, `Message.is_original_post` are retained as the foundation for
  the next milestone — public social (YouTube/Facebook/WhatsApp). They are
  intentionally unused (no producer) in this email slice. Action: confirm each
  emits valid SDL and breaks nothing now, and add a one-line note on each that it
  is social-milestone scope so a future reader does not read it as dead code.
- **M7. No tests.** Wire `messaging` into `tests/settings.py` (declare
  `ANGEE_CHANNEL_BACKEND_CLASSES` explicitly). Add manager tests: ingest
  idempotency on `(platform, external_id)`, null-byte strip, References thread
  merge, quote-edge direction, the M1 counter cases. This catches M1 immediately.
- **M8. Fragment ownership contradiction.** Content-addressed shared `Fragment`
  rows + per-owner `messaging/fragment` REBAC `read` → a second owner quoting the
  same text can't read the row, text vanishes. Decide: make Fragment unscoped/
  admin substrate (scope visibility via the owning `Part`/`Message`), or include
  owner in the dedup key (defeats dedup). Current shape is self-contradictory.
- **M9. Channel facet mislabels.** `ChannelType` has no display field, so the
  facet labels by `lastSyncStatus`. Give `ChannelType` a `name`/`label`, or pass
  `labelField` to `useRelationFacet`.
- **M10. Provenance leak (lift rule).** `managers.py:7,70` reference "the working
  model proved necessary" / "the working model matches…". Restate as standalone
  rationale (e.g. "Postgres rejects NUL in text/JSON columns").
- **M11. Quotation pass query volume.** `managers.py:317-367` is O(messages ×
  fragments); `_direction` re-queries `sent_at` already known. Hoist `sent_at`
  into the single `sharers` query, drop `_direction`'s query, batch the pass.
- **M12. Nits.** Memoize/hoist `defaultGroups` + `recordTabs` (match InferencePage);
  `ChannelBackend.icon="messages"` is unregistered (use `inbox`/`threads` or
  register); compose `RowsListView` instead of the hand-rolled `<ul>` in
  `ThreadMessagesTab`; document subject-less thread grouping.

## SHOULD-FIX — parties / CardDAV (the weak parse→map layer)

- **P1. v0 parse/map regressions — fields parsed-then-dropped while model+schema+
  form exist (dead on sync):**
  - **PHOTO → `Party.avatar` (`storage.File`)** — parse `PHOTO` (URI: fetch via the
    shared `http` client; inline base64 → bytes), ingest through the storage File
    owner. Currently zero PHOTO handling.
  - **birthday / anniversary** — add to `ParsedContact`, parse `BDAY`/`ANNIVERSARY`
    (multi-format `%Y-%m-%d`/`%Y%m%d`/`--MMDD`), set in `update_or_create`.
  - **Affiliation (org/title/role/department)** — add `role`/`department` to
    `ParsedContact`, parse `ORG[1]`+`ROLE`, upsert an `Affiliation` in
    `ingest_contact`. The entire `Affiliation` model+schema is dead on sync today.
- **P2. Digest auth.** v0 supported it; reconstruction is Basic-only. Add a
  `DigestAuthCredentialHandler` via the credential registry seam, or document the
  limitation.
- **P3. `sync_token` advertised-but-unimplemented.** `fetch_contacts` always does a
  full list+multiget; the token is stored, never used. Implement `sync-collection`
  REPORT (RFC 6578) or trim the docstring promise (`backends.py:28-35`).
- **P4. `is_preferred` (PREF) dropped** — `_labelled` returns only `(value, type[0])`;
  set `Handle.is_preferred` from `TYPE=PREF`. Minor.
- **P5. No test-connection/dry-run.** v0 validated before save; the connect dialog
  fires blind. Add a discovery probe to the connect mutation or a "Test" button.

## SHOULD-FIX — parties UX

- **U1. Person detail can't show handles/addresses — the parties half of "can't see
  handles / no vCard".** Add `recordTabs` (Handles, Addresses, Affiliations)
  composing a related list primitive on the Person detail, plus a standalone
  Handles list page (the deferred ask). Compose, don't hand-roll.
- **U2. Directory detail titles "ok"/sqid.** `display_name` is buried in `config`
  JSON, no `title` field resolves. Give `Directory`/`Integration` a real
  `display_name` column, set it in the connect mutation (it already receives
  `name`), expose on `DirectoryType`, add `<Field name="displayName" title
  readOnly/>`. Fixes the list (no name column) too.
- **U3. Connect CardDAV into the list toolbar.** `DataPage`/`ListView` expose no
  list-toolbar slot — the `ControlBand` sibling above is currently the only
  supported surface (integrate's `AddRepositoryControl` does the same). Owning-level
  fix: add `toolbarStart?: React.ReactNode` to `DataPageProps`, forward into
  `ListView`'s `DataToolbar` (`ListView.tsx:328`); then DirectoriesPage passes
  `toolbarStart={<ConnectCardDav/>}` and drops the sibling. RepositoriesPage adopts
  it too. Do NOT hand-roll a fake toolbar in the addon.

## SHOULD-FIX — framework (benefits every addon)

- **F1. People search `title` error.** `data-view-model.ts:67`
  `DEFAULT_TEXT_FILTER_FIELD = "title"` is hardcoded (standing TODO). The model
  metadata already resolves `recordRepresentation` (→ `displayName` for Person).
  Thread `recordRepresentation` into the search-field resolution
  (`textTerm`/`withTextTerm` + `list-view-utils.ts:176,272`) so the search filters
  on the model's real title field. Fixes Person and every non-`title` model.

## SHOULD-FIX — integrate security (delegated sub-audit)

- **I1.** `http.py:65-73` `allow_private=True` rejects only 2 hardcoded metadata IPs;
  docstring claims it rejects link-local but doesn't (Alibaba `100.100.100.200`
  etc. reachable). Reject `is_link_local` in private mode, or fix the docstring.
- **I2.** No `tests/test_integrate_http.py` — the 324-line SSRF boundary (incl. the
  `allow_private` path CardDAV uses) is untested. Add it.
- **I3.** `http.py:195` dial only the first resolved IP (no fallback); `:197` caller
  `headers` can override the pinned `Host` (put `Host` last); `_response_status`
  defaults 200 on unknown; GHE rejected because `_get` doesn't thread
  `allow_private` (`integrate_github/backend.py:157`).

## Suggested execution order

1. Messaging blocking M1–M5 (+ M7 tests alongside M1) — make the committed M1
   actually run and show senders. 2. M10 provenance (lift rule). 3. M6/M8 model
   decisions (escalate if needed). 4. P1 CardDAV parse→map to v0 parity. 5. U1/U2
   contact-detail + title. 6. F1 search (framework). 7. U3 toolbar slot
   (framework). 8. Schema gaps (S-items) + P2–P5 + I1–I3. 9. M9/M11/M12 polish.
