# Messaging contracts

Outbound transport implements `ChannelBackend.deliver(message)` and returns the
two-field `DeliveryOutcome(accepted, provider_id=None)` from
[`backends.py`](backends.py). Acceptance settles the existing row to `sent` and
records the provider id. An identity collision retains the client id and records
`delivery_conflict`, while still settling `sent`. A declined outcome is terminal.
Backends raise [`TransientDeliveryError`](delivery.py) only after definite
non-acceptance; they reconcile ambiguous publishes themselves. A missing
`retry_after` uses exponential backoff. The fourth refused provider attempt fails
permanently. Lock contention defers work without consuming an attempt.

`Message.deliver()` composes `queue_message_delivery`; the task composes
`Message.objects.claim_delivery` and `record_delivery`. Enqueue sets a bounded
delivery lease and publishes through the jobs owner's on-commit hook. The periodic
`release_held_messages` task releases due drafts/retries and recovers expired
leases, with SQL eligibility, skip-locked claims and a savepoint per row. Retry
settlement retains the enqueue-owned lease through the next scheduled attempt;
terminal settlement clears it. Queued rows without a lease are never released
automatically, even if they carry a due schedule.

A **held draft** is an untrashed outbound draft, as defined by
`Message.held_draft_fields()`. A future `scheduled_at` holds delivery until due;
a draft with no release instant requires operator approval indefinitely.
`Message.objects.send_held_drafts(selection, *, actor)` and
`discard_held_drafts(selection, *, actor)` accept the GraphQL owner's
`ActionSelectionInput(id, expected_revision)` selection and return ordered
`ActionResult` outcomes. They authorize one bounded selection through channel
`write`, check every revision, and use `many_actions` for per-row refusals. The
channel's grantable `replier` relation grants the narrower `reply` permission.

Moderation uses `trash_record` / `restore_record` and the native trash model.
Trashing an unsent outbound row cancels its delivery. Restoration never queues it
again. Generic message scalar updates are disabled; tags use the tags addon's
`tag` and `untag` mutations.
If removal races with an already accepted provider publish, settlement records
`sent` while retaining trash. Restoring that row never publishes it again.

`ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS` is contributed in
[`autoconfig.py`](autoconfig.py). Addons append their provenance keys there.
`Message.protected_local_keys()` owns the setting read. Ingest strips incoming
protected keys and retains their stored values, including on provider echoes;
echoes also preserve outbound sender, direction and authored parts. Trusted
Python callers write protected provenance with
`Message.objects.write_protected_local(messages, values, *, reason)`. Message
metadata is never a GraphQL write argument.

The GraphQL `MessageType.body_text` reads messaging's native BODY fragments and
caps its result at 4 KiB of UTF-8. Read access applies to the message before body
projection. Console held actions use the filterable `is_held` projection, capture
displayed revisions, confirm publication, and retain refused selected rows.
