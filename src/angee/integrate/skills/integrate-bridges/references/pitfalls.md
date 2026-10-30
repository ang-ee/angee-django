# Integrate pitfalls

Moved from the backend guide. The record-sync protocol and the integration
contracts stayed there: [Integration and worker contracts](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts). The Celery task pitfalls belong
to the `jobs-tasks` skill.

- **Querying every ancestor of a bridge queues the same connection twice.** The
  [final-MTI scheduling rule](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
  selects the one row through
  [`Integration.concrete_type`](../../../../../../src/angee/integrate/models.py);
  a scheduler that walks the parent kinds (Channel and its public Feed child)
  enqueues both. Keep parent-scoped message access on the declared Channel
  relation instead of re-querying by kind.
- **Native bridge SDKs can abort the interpreter.** Declare process isolation on
  the owning `LiveBridgeImpl`; use the shared integration process host rather
  than serializing selected calls in Python. The session child owns the store
  and account locks until native cleanup completes or the process exits.
  Infrastructure crashes stay retryable; terminal account outcomes retain their
  existing runtime-error latch.
- **Never mix `select()` with buffered `readline()` on a subprocess pipe.** A
  buffered wrapper may consume several complete records while the file descriptor
  becomes non-readable, stranding those records behind the readiness check; it
  can also block mid-line past the stop cadence. Read raw bytes with `os.read()`
  into a manual newline accumulator and drain complete buffered lines before the
  next readiness wait.
- **Never create a second loop around a coroutine bound to the live client.**
  The vendor loop is created and run where the vendor connects, on the
  connection thread ([loop ownership](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)).
  When task-thread work must call an async vendor hook (for example,
  downloading media during ingest), schedule the coroutine with
  `asyncio.run_coroutine_threadsafe` onto that owning loop and wait with a
  finite timeout; a fresh `asyncio.run()` around a client-bound coroutine fails
  or deadlocks.
- **A child's second intent axis arrives unset on rows the generic verbs move.**
  The generic lifecycle actions move an `int_…` row without the owning addon in
  the call path, so a live desired-state or subscription the child added is
  unset on rows that arrive that way. A reconciler that selects on its own axis
  and trusts something else to have set it never runs for those rows; the
  [second-axis contract](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
  makes it select on the lifecycle and drive its own axis from it. Test the one
  state that runs, not the one state you happen to stop on. `angee.integrate.tasks`
  is the reference: `ensure_bridge_sessions` selects CONNECTED and reconciles the
  live desire to it, and gates on `runtime_status` so a known-broken handshake
  is not redispatched forever. Reconcile only when the axes disagree: the write
  has no dirty check and publishes a subscription event, so an unconditional one
  broadcasts a no-op edit per row per tick.
- **A repair verb that only declares the target lifecycle clears nothing.**
  `set_lifecycle` returns early when the row already reads the target, so a
  CONNECTED+ERROR row repaired by a verb that only declares CONNECTED keeps its
  `runtime_status=ERROR` latch and gets exactly the one dispatch the verb
  enqueues directly; lose that (worker down, queue saturated, a restart) and the
  reconciler skips the row forever. The
  [latching-gate contract](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
  makes the verb clear the error itself: `resume_channel_pairing` reports OK
  unconditionally for this reason. A private health key is a further axis the
  generic verbs cannot clear, so it reintroduces the same latch on the generic
  path.
- **OAuth/OIDC outbound requests must send an honest, non-browser User-Agent.**
  Anthropic's token-endpoint edge 429s spoofed browser/curl User-Agents with a
  `rate_limit_error` (before any auth check) and 403s urllib's `Python-urllib`
  default; an honest client UA passes. `angee.integrate.oauth.client` owns the value
  (`USER_AGENT`); never reintroduce a browser spoof or fall back to urllib's
  default.
- **httpx and httpx2 objects do not mix.** An `httpx2.Client`, which Authlib's
  `OAuth2Client` is from 1.8.0, asserts its own stream type inside the
  transport, so an httpx transport fails on the first real request while
  `MockTransport` tests pass. Integrate's outbound HTTP is httpx2 end to end
  ([`angee.integrate.http`](../../../../../../src/angee/integrate/http.py)). Cover a
  transport or HTTP-library change with a real-socket test such as
  `test_oauth_protocol_round_trips_over_the_real_pinned_transport`.
- **Anthropic's JSON OAuth token exchange must echo redirect `state`.** Standard
  OAuth validates state before the token POST and does not send it, but
  Anthropic's public-client JSON token endpoint rejects that request as malformed
  without the state field. Keep the exception inside
  `angee.integrate.oauth.client`'s JSON shim; do not move it to the frontend,
  generated callback route, or generic form-token path.
- **Retire conversion inputs only after proving history parity.** Integrate's
  manifest orders its history fold before its store retirement declaration.
  The retirement verifies all converted observations and failures before native
  `DeleteModel` operations; reverse rebuilds from ordered link history and retained stream state, with legacy IDs and native-only
  distinctions lost as documented in the migration.
- **Review enrolled local rows before enabling two-way record sync.** The
  [record-sync driver](../../../../../../src/angee/integrate/README.md) may publish targeted
  links after the first baseline. Directory membership belongs to RecordLink;
  creating a Party never enrolls it in a remote address book.
