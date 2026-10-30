# Authorization pitfalls

Moved from the backend guide. The contracts stayed there:
[REBAC](../../../../docs/backend/guidelines.md#rebac), [GraphQL actor and write contracts](../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)
and [Direct record access](../../../../docs/backend/guidelines.md#direct-record-access).

- **Explicit delete preflight plus elevated destructive work must test both branches.**
  Storage's soft-delete path and messaging's threaded-record delete path check the
  public `delete` permission themselves, then run the owned destructive work under
  `system_context` ([elevated destructive work](../../../../docs/backend/guidelines.md#rebac));
  the library's denial-audit signal is skipped on the explicit deny branch by
  design, so add a deny-path regression whenever you use this shape.
- **zed exclusion binds loosest.** Parenthesize `(a - b) + c` when combining
  exclusion with union.
- **In test-client logins pass the backend** —
  `force_login(user, backend="angee.iam.auth.ModelBackend")`; the default backend
  order is chosen for runtime authentication concerns and may not be the session
  reload backend a focused test wants.
- **A gated factory that uses `sudo()` must restore the actor before returning.**
  The [actor-scope rule](../../../../docs/backend/guidelines.md#rebac) owns the
  contract. Capture `current_actor()` before the elevated block and rebind the
  returned instance with `.with_actor(actor)` after save.
