# Web forms and messaging

A form's responses posted as messages on a messaging channel. Neither addon
depends on the other: web forms keeps responses without messaging, and messaging
carries no form. A host that wants a response to reach a conversation, and
whatever reads that conversation, composes this bridge.

## The `message` target

A form selects the target by key and names where its responses go:

```yaml
target_class: message
target_config:
  channel: <the channel's public id>
  slug: enquiries
```

- `channel` is a channel with the `webform` backend, by its public id: never
  blank, and stored without the spaces around it, so one channel is always
  stored the same way. When a form whose target or configuration changes is
  validated (`full_clean`), as the console and the resource loader do, a
  channel that is gone or has another backend is refused
  under `target_config` ([`MessageTarget.clean`](targets.py)): no response could
  be posted on it. For an acting user the channel is read through their own
  access, so one they cannot read is refused as a missing one; system work reads
  it elevated, as delivery does. Posting on a channel is messaging's to allow,
  so someone may point a form at one only if they may also write on it
  ([`MessageTarget.permits`](targets.py) asks the channel's own `write` check).
  An addon that declares the form as a
  resource row ([a form an addon declares](../webforms/README.md#a-form-an-addon-declares))
  names a channel its resources declare by xref (`channel: acme.intake`):
  `MessageTargetConfig` declares `channel` a relation to `messaging.Channel`, so
  the load stores that channel's public id, and refuses an xref that names no
  channel. The relation is the bridge's: web forms reads the target's schema and
  never learns what a channel is.
- `slug` names the form's messages on that channel. Each response is the message
  `webform:<slug>:<submission id>`, so delivering one again finds the message it
  posted. All of them share the conversation `webform:<slug>`, titled with the
  form's name. Changing the slug starts another conversation.
- One channel and slug take one form, since two would share those message ids.
  A second form is refused when it is configured (`Form.clean`, under
  `target_config`), and by the table when two are saved at once
  ([`FormConversation`](models.py), which the bridge contributes to the form).
  A form that leaves the target for another gives its configuration up (the
  `store` target takes none, and refuses one left behind); the channel and slug
  are then free, and the same form may take them again.

[`MessageTarget.deliver`](targets.py) hands the response to messaging's one
ingest path (`Message.objects.ingest`) and returns the message, which the
response links as the record it became. The bridge declares
`messaging/message` as a type a response can become
([`permissions.extends.zed`](permissions.extends.zed)): web forms links a
response only to a record of a declared type, and when that message is deleted
the response loses its personal answers and the link.

A response is refused in three ways. Each is kept on the response as
`refused: <code>`, where staff see it, and is not tried again by itself:

| Code | The refusal | What staff do |
|---|---|---|
| `channel_missing` | The channel the form names is gone, or is no webform channel | Point the form at a webform channel, then deliver again |
| `message_exists` | The channel already holds a message under this submission's id that the response did not make: one received before forms had responses, whose id was sent again. Posting would replace it | Dismiss the response; the earlier message stands |
| `message_refused` | Messaging will not take the message as it is: its envelope is past messaging's size bound | Dismiss the response, or shorten what the form asks |

Web forms' own `config_invalid` (its README) applies here too: a form whose
stored channel and slug no longer validate is refused before the channel is
read. Any other error is a failure, which web forms tries again.

## The envelope

The message's metadata carries a `webform` envelope, which
`Message.webform_submission()` reads:

| Key | Value |
|---|---|
| `slug` | The target's slug |
| `schema_version` | The number of the version the answers are to |
| `submission_id` | The client's id for the submission |
| `answers` | The answers by question name |
| `unverified_submitter_email` | The address the respondent claims, or null |

The claimed address is the answer to the version's `contact_question` when that
question is a `text` question with `inputType: email`. It leaves `answers` and
the body, and it proves no identity. The answer to any other contact question
stays an answer.

The body lists each answer under its question's title in the form's default
language, in the order the form asks them. A choice is shown by its code; the
text a respondent gave for "Other" stands in for that code.

## What erasing does not reach

A form that posts on a channel keeps a copy of the answers in messaging: the
message's envelope and its body, with the claimed address. Erasing the response
(`Response.erase`, `Response.objects.erase_for_record`) blanks what web forms
holds and does not reach that copy, because messaging has no verb that redacts
or deletes an ingested message. Closing it needs a target erase hook in web
forms and a redaction verb in messaging; both are a follow-up.

## The `webform` channel backend

[`WebformChannelBackend`](backends.py) is a channel that pulls nothing and
delivers nothing: the target is its one source. A channel row stores the key
`webform`, so a host that holds such a channel composes this bridge. Without it
the database check `angee.E052` names the rows and says to install it.

## Channels that held their own form

Earlier releases kept a public form on its channel row: a slug, a published flag
and a FormSpec. A project that holds such channels copies the migration template
[`webform_channels_to_forms`](compose_migrations/messaging/webform_channels_to_forms.py)
into `runtime/messaging/migrations/`, replaces its two placeholder dependencies
with its own leaves, and applies it. The order matters:

1. Delete any autodetected migration that removes the channel's form columns.
   The drop guard refuses to author one only at `makemigrations`, and only on a
   database that holds a form.
2. Copy the template in, pinned after the project's messaging leaf and its
   webforms initial migration. `makemigrations messaging` then reports no
   changes.
3. Run `manage.py check_webform_channels`. It judges the channels as `migrate`
   will, prints what the move would refuse and what it would leave out, and
   changes nothing. Where the form columns are already gone, or no webform
   channel holds a form, it says there is nothing to move and asks for no
   template.
4. Run `migrate`, then `rebac sync`.

`migrate` refuses the wrong order by itself, also under `provision`, which skips
system checks. The columns are messaging's, so the refusal is too, and it holds
on a project that has not installed this bridge
([`angee.messaging.webform_columns`](../messaging/webform_columns.py)): a run
whose plan removes the form columns with a migration that neither is the move
nor follows it is stopped before anything is applied, while a webform channel
of that database holds a form. `check --database` says the same beforehand as
`angee.E054`. That guard is interim: deleted from messaging once the framework
pins django-compose 0.1.0.dev14, whose drop guard judges pending removals at
migrate time. The order above stays the instruction.

The bridge keeps nothing of the guard. Its template declares that it is the
move (`moves_webform_channels = True` on its `Migration`), so a project's copy
is known under whatever name it has, and it imports messaging's one judgment of
which channels hold a form
([`angee.messaging.webforms`](../messaging/webforms.py)), which the dry run
reads too. A copy of the template taken before the mark existed lacks it and
must be copied again; `migrate` refuses it as a removal that is not the move.

Each channel becomes a form with its first version and its address, posting on
the same channel under the same slug, and the channel's form columns are
removed. No response is made: the submissions a channel received stay the
messages they are. A migration writes through historical models, so no verb of
a form runs: the template judges each version with the web forms' own rule
(`validate_publishable` for a published form, `validate_version` otherwise)
before it writes it, and refuses a channel that rule refuses, by name. The
template's docstring states what carries over, what it refuses and what it
reports. It does not reverse: going back means restoring a backup taken before
it and running the previous code.

## Intake

`angee.intake` captures a message on a channel that has an intake queue, and it
reads the envelope: `intake_field_map` maps answers to a request's fields by
question name, and `intake_requester_domains` reads the claimed address.
