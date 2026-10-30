# Task pitfalls

Moved from the backend guide. The contracts stayed there: [Integration and worker
contracts](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts). The bridge and live-session
pitfalls belong to the `integrate-bridges` skill.

- **A long-lived Celery task needs the three-check wake loop.** The
  [session-task contract](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
  says it stops within one reconciler tick; the loop that achieves it wakes on a
  bound (shorter than the tick) and checks: (1) the persisted desired-state, so a
  cooperative stop never waits on an idle socket; (2) a process-local
  `worker_shutting_down` event, or a warm SIGTERM wedges behind the pool's
  blocking join until SIGKILL; (3) that it still holds its advisory lock —
  Postgres advisory locks are connection-scoped, and a DB reconnect drops the
  lock under a live process, so the holder must exit for a clean reconciler
  restart instead of racing a duplicate against shared state. The threads pool
  enforces **no** time limits, so queue isolation is the protection and
  `time_limit=None` on the task is only defense-in-depth
  (`angee.integrate.session` + `angee.integrate.tasks` are the references).
- **Celery periodic tasks accept `timestamp` when a scheduler supplies one.**
  A wrapper that requires it breaks under static beat ticks; one that rejects it
  breaks under tests and scheduler backends that inject a Unix timestamp. Keep
  both shapes ([worker contracts](../../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)).
