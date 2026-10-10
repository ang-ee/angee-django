# Public feed contracts

[`FeedBackend`](backends.py) is an integrate stream adapter. Source backends
implement `streams(*, deadline=None)` and `extract(stream, page_bound, *,
deadline=None)` using integrate's `StreamDefinition` and `StreamPage`. Integrate
owns durable `SyncStream` cursors, baseline completion, bounded page draining,
quarantine and reconciliation. Feeds inherit that driver; there is no feed-local
cursor or sync loop. Connection discovery composes integrate's
`discover_connection` and `apply_discovery` hooks. Feed adapters must discover
their identity; `FeedBackend.apply_discovery` writes it through the handle owner.

Posts supplies `record_key`, `apply_record` and `finish_page`. Each parsed post
lands through [`land_posts`](ingest.py), which composes messaging's one ingest
owner. Its overlay and provider trash state are applied before live events;
trashed rows never emit `message_ingested`. Cross-post relations finish after
successfully landed page records become visible. A source that unhides a row
restores only trash it previously applied, preserving operator moderation.

`Feed.live_since` is stamped once by `Feed.binding_finished()` after integrate's
committed `binding_finished(sender=type(bridge), instance=bridge)` signal. Each
record is historical when its sent timestamp is absent, precedes the horizon, or
the feed has no horizon yet. History and activity streams therefore classify
their records independently. The addon-owned `feed_live_since` data migration
stamps existing bound feeds after the field exists; it changes no message status.

`MessagePublic.reply_to_comment(*, body, actor, local=None, creation_key=None)`
owns feed reply policy on the shared Message row. It composes
`reply_state()` under the comment's row lock on insertion, after replay, excluding
other runs and native platform answers. The same owner predicate serves workflow
eligibility; discarded and failed replies do not count. Reply preparation composes
`Message.objects.compose_reply` with the final hold state at insertion and native
creation-key replay. The actor needs readable context and channel `reply`.
Unreadable comments raise `PermissionDenied`. New attempts on answered comments
raise `CommentAnswered`; matching replays return their existing reply first.
`Feed.reply_hold` is operator-editable hours: null holds for approval indefinitely,
positive hours schedule a held draft, and zero queues immediate delivery. Replays
preserve the original hold and settlement. `local` is trusted Python provenance,
never an agent GraphQL argument. Drafts advance thread counters only after publish.

Source backends publish replies with `deliver(message) -> DeliveryOutcome`; the
feed Channel adapter holds integrate's bridge advisory lock through publish and
settlement. Polling and verified webhook landing must compose that same lock.
Quota is the integration-scoped ledger:
`Quota.objects.consume(*, integration, units, limit, now=None) -> bool`.

The read tools are `read_comment_thread(sqid)` and `read_message_text(sqid)`, both
registered through the existing MCP GraphQL seam over actor-scoped
`messages_by_pk`. Plain body projection belongs to messaging. `is_original_post`
is contributed through the model's `hasura_filterable_fields` declaration.

Feeds use integrate's `FORM` creation catalogue and existing add-integration page.
Backend implementation defaults supply vendor and platform identity. The saved
feed's `posts.Feed#actions` Connect child composes integrate's shared
`ConnectOAuthButton` with `CONNECT_RECORD_FIELDS` and its `ConnectIntegration`
document. It returns to that feed after OAuth and reloads the record.
