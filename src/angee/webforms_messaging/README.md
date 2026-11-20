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

- `channel` is a channel with the `webform` backend. A response whose form
  names another kind of channel, or one that does not exist, fails its delivery,
  and the failure is recorded on the response where staff see it.
- `slug` names the form's messages on that channel. Each response is the message
  `webform:<slug>:<submission id>`, so delivering one again finds the message it
  posted. All of them share the conversation `webform:<slug>`, titled with the
  form's name. Changing the slug starts another conversation.

[`MessageTarget.deliver`](targets.py) hands the response to messaging's one
ingest path (`Message.objects.ingest`) and returns the message, which the
response links as the record it became.

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

## Channels that held their own form

Earlier releases kept a public form on its channel row: a slug, a published flag
and a FormSpec. A project that holds such channels copies the migration template
[`webform_channels_to_forms`](compose_migrations/messaging/webform_channels_to_forms.py)
into `runtime/messaging/migrations/`, replaces its two placeholder dependencies
with its own leaves, and applies it. Each channel becomes a form with its first
version and its address, posting on the same channel under the same slug, and
the channel's form columns are removed. The template's docstring states what
carries over, what it refuses and what it reports. It does not reverse.

## Intake

`angee.intake` captures a message on a channel that has an intake queue, and it
reads the envelope: `intake_field_map` maps answers to a request's fields by
question name, and `intake_requester_domains` reads the claimed address.
