# Mailbox Channel Design

**Status:** Approved on 2026-07-13

## Goal

Replace the IMAP-specific channel shape and hard-coded mailbox exclusions with a
protocol-neutral mailbox integration. `MailboxChannel` is the persisted channel
kind; IMAP is the first interchangeable transport backend, with JMAP and POP able
to contribute later without depending on IMAP.

No migration files are generated and no migrations are run in this change. A
separate custom migration hook will move existing channel data after it lands.

## Ownership

- `angee.messaging` owns the generic `Channel`, neutral message ingest, and
  manual/no-source behavior.
- A new optional `angee.messaging_integrate_mailbox` addon owns the concrete
  `MailboxChannel` MTI child, the mailbox-selection field and matcher, and the
  protocol-neutral `MailboxBackend` contract.
- `angee.messaging_integrate_imap` depends on the mailbox substrate and owns the
  IMAP transport, parsing, connection mutation, seeded vendor, and connect UI.
- Django model fields own persisted defaults, labels, help text, widget hints,
  and validation. Resource metadata carries those facts to the frontend.
- The backend implementation class owns protocol behavior and protocol-specific
  defaults for fields shared by every mailbox channel. It does not define a
  parallel pseudo-model schema.

## Model Shape

`MailboxChannel` is a materialized Django child of `messaging.Channel`. It owns
the common mailbox account fields:

- `backend_class`: an `ImplClassField` whose configured implementations subclass
  `MailboxBackend` (`imap` now; `jmap` and `pop` later).
- `host`, `port`, and `security`: protocol-neutral server connection facts.
- `mailboxes`: a JSON-backed list field containing include/exclude patterns.
- `own_addresses`: a JSON-backed list of addresses used for direction detection.

The integration parent continues to own the credential, owner, lifecycle, and
audit identity. The bridge parent continues to own generic cursor and sync state.
Passwords remain credential material and never become mailbox model fields.

The generic `Channel` no longer selects arbitrary source implementations. A
direct `Channel` is the manual/null channel; concrete channel children override
the backend behavior. This puts the mailbox backend discriminator on the row
whose persisted shape it actually selects.

## Mailbox Pattern Contract

`mailboxes` is one pattern per list item. Matching is case-insensitive and uses
shell-style wildcards.

- A normal pattern includes matching mailboxes.
- A pattern prefixed with `!` excludes matching mailboxes.
- A mailbox is selected when at least one include matches and no exclusion
  matches. Exclusions always win.
- Patterns match the full mailbox name, its final delimiter-separated segment,
  and advertised protocol roles/special-use flags such as `\Junk` or `\All`.
- An empty list selects nothing.
- Non-selectable server entries remain unavailable transport facts, not policy.

The field default is explicit and UI-visible:

```text
*
!\All
!\Junk
!\Trash
!\Drafts
!All Mail
!Junk
!Spam
!Trash
!Drafts
!Deleted Items
!Deleted Messages
```

Users can remove any exclusion, including `!\Junk`, to sync that mailbox. POP
backends can expose a single synthetic `INBOX` descriptor and therefore use the
same selection contract.

The Django field owns a concise label and help text explaining `*`, `!`, matching
targets, case-insensitivity, and empty-list behavior. It validates that the value
is a list of non-empty strings and normalizes surrounding whitespace.

## Backend Contract and IMAP Adaptation

`MailboxBackend` extends the neutral `ChannelBackend` with mailbox descriptor
discovery. A descriptor carries the full name, hierarchy delimiter, and normalized
roles/special-use flags. The substrate applies `MailboxChannel.mailboxes` to those
descriptors; protocol implementations do not embed selection policy.

The IMAP backend converts `LIST` results into descriptors, then reuses the shared
selector. The current hard-coded junk/trash/drafts name sets, the `\All`
preference, and `skip_mailboxes` are removed. Fetching, UIDVALIDITY, UIDNEXT,
partition cursors, body budgets, retry behavior, and MIME parsing remain IMAP
implementation concerns.

The existing IMAP connect flow remains backend-specific because authentication
flows need not be common across IMAP, JMAP, and POP. It creates a
`MailboxChannel` with `backend_class="imap"` and cleans model-owned fields before
saving. Omitted mailbox input uses the model field default; an explicitly cleared
input persists `[]`.

## MTI Bridge Scheduling

The current bridge registry enumerates every concrete `Bridge` subclass. A naive
`MailboxChannel(Channel)` addition would therefore schedule the same identity as
both its parent and child.

Bridge iteration is changed to return the most-specific materialized row for each
identity. Parent querysets exclude primary keys present in concrete bridge
descendants; leaf querysets include them normally. This rule is shared by due
scheduling, queueing, and the manual `syncIntegration` action. Direct manual
`Channel` rows still run as channels, while mailbox rows run only as
`MailboxChannel`, ensuring the backend receives the typed child instance.

## Metadata and UI

Resource field metadata gains field-owned `label` and `helpText` values projected
from Django `verbose_name` and `help_text`. The TypeScript metadata artifact
preserves both. Shared field-descriptor defaulting uses `label` and maps
`helpText` to the widget description without overriding explicit UI props.

The IMAP connect dialog resolves its `mailboxes` descriptor and initial value from
`messaging_integrate_mailbox.MailboxChannel` metadata. It retains only
interaction-specific copy in the IMAP frontend; the mailbox syntax instructions
are rendered from Django metadata below the textarea.

## Error Handling

- Invalid persisted mailbox values fail model/service validation before a channel
  is activated.
- Unknown or incompatible mailbox backend keys fail through `ImplClassField`
  registry validation.
- A valid pattern that matches no current mailbox is not an error; mailbox sets
  can legitimately change between runs.
- IMAP discovery or selection failures continue to fail the sync loudly and feed
  the existing bridge error telemetry.

## Verification

Tests cover:

- mailbox field defaults, normalization, validation, label, help text, and widget
  metadata;
- full-name, leaf-name, special-use, wildcard, case-insensitive, exclusion-wins,
  and empty-list selection;
- IMAP descriptor projection with no hard-coded junk or `\All` policy;
- explicit inclusion of junk by removing its configured exclusion;
- the IMAP connect mutation creating a `MailboxChannel` and distinguishing omitted
  mailboxes from explicit `[]`;
- metadata propagation from Django field to the rendered dialog help text;
- parent/child bridge scheduling selecting exactly one most-specific row;
- existing IMAP cursor, partition, retry, parsing, and ingest behavior after the
  model move.

No migration generation or migration execution is part of verification.

## Out of Scope

- JMAP and POP implementations.
- The custom data-migration hook and migration files.
- A protocol-neutral connection UI or authentication flow.
- Changes to message ingest or mailbox membership modeling beyond the existing
  single `metadata.mailbox` value.
