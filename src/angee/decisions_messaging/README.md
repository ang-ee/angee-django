# Decision mail

Open decisions are mailed to the people they can be put to, so a question does not
wait unseen. The bridge sits above [decisions](../decisions/README.md) and
[message templates](../messaging_templates/README.md); decisions itself stays free
of messaging. The [models module](models.py) owns the contracts below, and its
docstrings carry the exact API. "Mailed" means recorded on messaging's system
channel and queued; delivery follows the commit, on messaging's side.

## What is mailed, and when

- **When a question is asked.** `decision_asked` schedules, once the ask commits, a
  `decisions_messaging.mail_decision` task that mails the question to each person in
  its kind's audience (below). A rolled-back ask schedules nothing. Only questions
  asked after the addon is installed are mailed this way; older open questions reach
  people through the digest.
- **When its reminder is due.** `decision_reminder_due` does the same, once per
  question, after the reminder stamp commits.
- **Once a day.** The `decisions_messaging.mail_digests` beat entry runs at
  `DIGEST_HOUR` (07:00 in Celery's time zone, UTC). Each active person is mailed one
  digest of the open questions whose audience includes them, older ones included:
  the same audience the asked and reminder mails go to, read from the person's side.
  Each person's list is their own scoped read, a few statements whatever the number
  of open questions. A person whose address is refused, none included, is
  counted as the asked and reminder mails count them. The questions no asked or
  reminder mail reached them about in the day before the run come first, then the
  rest, due first; at most `DIGEST_QUESTION_LIMIT`, with a count of the others. A
  person with nothing waiting gets no digest, and neither does one every waiting
  question was mailed to in the day before the run (`DIGEST_WINDOW`).

A question answered, withdrawn or no longer readable to a person when their mail is
sent is not mailed to them.

## Who is mailed

A question's mail goes to the audience its decision kind has in
`ANGEE_DECISIONS_MESSAGING_AUDIENCES`, a mapping of kinds to audiences in which
`"*"` covers every kind not named. A key is a `Decision.kind` exactly as the asking
code asks it, not its label. Its default, `{"*": "deciders"}`, mails every question
to its deciders. Each audience is one decisions rule read from both sides,
so a question's mail and a person's digest always agree (`MailAudience`):

- **`deciders`:** `Decision.objects.deciders()` and `put_to(person)`, the active
  people who may both answer it (`act`) and read it. The administrator arm and every
  owner's `owner_eligible` arm count; a requester is mailed only when another arm
  lets them decide; someone who holds `act` without `read` (an owner arm that grants
  answering alone) is not mailed, since the mail would name a question whose page
  they cannot open. Service users and inactive accounts are left out. Everyone who
  holds `act`, the administrator arm included, is mailed on every occasion, the
  digest too: neither side has `waiting_on`'s "assigned to me or unassigned" clause,
  so administrators get the mails, and the digest entries, of questions assigned to
  others.
- **`administrators`:** `Decision.objects.administrators()` and
  `for_administrator(person)`, the platform administrators among the question's
  deciders, whoever it is assigned to. An administrator who may not answer a
  question (an owner's `owner_managed` takes the administrator arm's `act` away) is
  never mailed about it.

A deployment that has its administrators set things up while one kind still goes to
the people who decide it maps its default to administrators and that kind to its
deciders. A deployment whose kinds are sentences names each by the kind exactly as
it is asked, as `{"*": "administrators", "Approve posting": "deciders"}` does. A
mapping that is not one of kinds to `deciders` or `administrators` is refused at
start-up (`decisions_messaging.E001`), naming the entry it refuses.

## What each person reads

Each mail is written from values read for that one person, in their own scope:
`Decision.concern_labels(person)` names the concerned records they read (the first
`QUESTION_RECORD_LIMIT`, with a count of the rest), `Decision.facts_for(person)` the
question's facts they are shown (the first `QUESTION_FACT_LIMIT`, with a count of the
rest), and `Decision.requester_name(person)` who asked, so a mail never shows a
label, a fact or a name the person could not read in the app, even after their read
was revoked. The facts are a short list of label and value (`DecisionFact.as_text`,
the card's text form), marked "(corrected)" or "(unverified)" when the value is not
the source's; a fact a widget reads gives its bare value. A fact's label and value
are each cut with an ellipsis to a length derived from the renderer's limits
(`VALUES_JSON_LIMIT`, `VALUE_STRING_LIMIT`) for the questions the mail lists, so the
fullest mail stays inside them: `FACT_TEXT_LIMIT` in an asked or reminder mail, which
carries one question, and `DIGEST_FACT_TEXT_LIMIT` in the digest, which shares the
same budget among up to `DIGEST_QUESTION_LIMIT`. The question's reason is cut the
same way, to `REASON_TEXT_LIMIT` or `DIGEST_REASON_TEXT_LIMIT`. The app shows them
whole. This holds
whatever authority `decision_asked` was sent under, which a supersede elevates. The
question's kind, reason, due time and choices are the same for everyone it is put to.

Each mail is a shipped message template sent with
`MessageTemplate.objects.send_as_system` as named system work, one reason per
template (`decisions_messaging.decision_asked.send`,
`decisions_messaging.decision_reminder.send`,
`decisions_messaging.decision_digest.send`), with no causing actor and in the
person's language (`effective("template", key, person=<their parties Person>)`). The
rows are `decisions_messaging.decision_asked`, `decisions_messaging.decision_reminder`
and `decisions_messaging.decision_digest`. Each includes the part
`decisions_messaging.question`, which says what one question is about and ends with
one "Open the decision" button. A deployment overrides any of them as it overrides
any shipped row.

The button links the question's page in the app: the deployment's public origin
(`angee.base.origins.configured_public_origin`) joined with
`Decision.get_absolute_url()`. Every send names it as what the mail must show
(`send_as_system`'s `must_print`): `question.url` in an asked or reminder mail, and
`questions.*.url` in a digest, which lists a question only with its link and is not
sent for an empty list. The person signs in if needed and decides there; no link in
a mail decides anything, and none carries a token. The values a mail is written from
are kept with its message, which administrators read, so they hold nothing secret.
The frames that hold a link or an address hide every local from an error report
(a bare `sensitive_variables()`), and no error this addon raises names either.

## Mailed once

`DecisionNotice` records each person a question was mailed to, once per occasion
(asked, reminder), and `DigestNotice` each person mailed a digest, once per UTC day;
both share `MailNotice`, whose manager writes a notice once
(`MailNoticeManager.record`).
Each person is mailed in a durable transaction of their own: the send and its notice
together, never one transaction around many sends, so the mail verbs run outside any
transaction, as their tasks do. A notice whose mail stands means it is not sent
again, so a retried task mails nobody twice; when two runs race, the notice's unique
key decides and the loser's mail rolls back with its notice. What a notice's mail
came to is the delivery state messaging recorded for its message:

- **Queued or sent:** it stands.
- **Failed:** it reached nobody. Messaging never retries system mail, so the
  occasion's next run mails the person again and points their one notice at the new
  message; there is never a second notice. Of two runs re-sending at once, the
  notice's compare on the message it held decides, and the loser's mail rolls back.
  A failed asked or reminder mail is re-sent only when that occasion runs again (a
  re-run of its task, or a retry the task makes for another person's refusal); the
  daily digest is the backstop, since its overlap rule counts only mails that stand,
  so it lists that question as never mailed. A digest notice whose mail failed
  leaves that day open, and the day's next run sends it again.
- **Uncertain:** it may have been delivered, so this addon never sends it again and
  never surfaces it. Telling administrators once about a system mail whose delivery
  is uncertain is messaging's open item, for every sender of system mail; this addon
  has no "send again" act, so sending one again is a person's deliberate act outside
  it.

The message is messaging's to keep or purge: purging it empties the notice's link and
keeps the notice, which then stands. The ledgers are internal: no public id, no
GraphQL surface and no REBAC resource type, since nothing outside this addon reads
them; who was mailed is also on messaging's system channel, which administrators
read.

## When a mail cannot be sent

- **A person's address is refused** (none, or one system mail refuses:
  `Message.objects.clean_system_recipient` before the send, or the send keyed `to`):
  that person is skipped and counted in the task's log, and the others still go.
- **One person's mail fails** (a deployment's override that refuses to render in their
  language, a value too large for the renderer): it is logged by their primary key
  and the refusal's code and counted, the others still go, and the task fails once
  everyone was tried, so the failure stays visible.
- **A template row cannot show what the mail needs** (`value_not_printed`, or a link
  it cannot carry, `value_not_linkable`): that is the row's or the deployment's
  fault, never a person's skip. The others are still mailed, and the run fails once
  with `DecisionMailUnsendable`, worded for an administrator.
- **The deployment cannot mail at all.** Each run refuses before anyone is mailed,
  in order: no public origin (`PublicOriginUnavailable`), system mail not configured
  (`Channel.objects.system_mail_configured()`, as `DecisionMailUnsendable`), and a
  missing shipped row (`DoesNotExist`). These fail
  their run once, in the worker's log, and are not retried. A system channel
  that is missing or not connected (`SYSTEM_CHANNEL_MISSING`,
  `OUTBOUND_EMAIL_CHANNEL_MISSING`) is refused the same way per person, but an
  operator can restore it while the task waits, so the task is retried 5, 15 and 45
  minutes later (`RETRIED_REFUSALS`, matched on messaging's own refusal classes). A
  database or broker failure is retried on Celery's short backoff.
- **The task reaches its soft time limit.** Celery's `SoftTimeLimitExceeded` stops the
  run at once, before the next person; the people already mailed keep their notices,
  and the next run mails the rest.

## Not covered

A question a workflow step holds is never reminded by `remind_due`, so it gets no
reminder mail; the digest still lists it while it waits on someone. A reminder whose
task was lost to a broker failure at commit is not sent again, since its stamp is
kept; the digest lists the question.

**Debt in `angee.messaging_templates`:** the renderer measures a mail's values as
JSON (`VALUES_JSON_LIMIT`), and it offers no public measure of the size of a value's
JSON. So the fact cuts count characters, as Django's `Truncator` does, while JSON
writes a quote, backslash or newline in two and another control character in six. A
mail whose facts are mostly such characters can still pass `VALUES_JSON_LIMIT`, and
that person's mail then fails as a defect. It goes when `messaging_templates`
publishes that measure and the cuts compose it.
