# Decision mail

Open decisions are mailed to the people they can be put to, so a question does not
wait unseen. The bridge sits above [decisions](../decisions/README.md) and
[message templates](../messaging_templates/README.md); decisions itself stays free
of messaging. The [models module](models.py) owns the contracts below, and its
docstrings carry the exact API. "Mailed" means recorded on messaging's system
channel and queued; delivery follows the commit, on messaging's side.

## What is mailed, and when

- **When a question is asked.** `decision_asked` schedules, once the ask commits, a
  `decisions_messaging.mail_decision` task that mails the question to each of its
  deciders. A rolled-back ask schedules nothing. Only questions asked after the
  addon is installed are mailed this way; older open questions reach people through
  the digest.
- **When its reminder is due.** `decision_reminder_due` does the same, once per
  question, after the reminder stamp commits.
- **Once a day.** The `decisions_messaging.mail_digests` beat entry runs at
  `DIGEST_HOUR` (07:00 in Celery's time zone, UTC). Each active person is mailed one
  digest of the open questions put to them (`Decision.objects.put_to(person)`, the
  person's side of `deciders`) that are waiting on them
  (`Decision.objects.waiting_on(person)`, the inbox's "Waiting on me"), older ones
  included. Each person's list is their own scoped read, a few statements whatever
  the number of open questions. A person whose address is refused, none included, is
  counted as the asked and reminder mails count them. The questions no asked or
  reminder mail reached them about in the day before the run come first, then the
  rest, due first; at most `DIGEST_QUESTION_LIMIT`, with a count of the others. A
  person with nothing waiting gets no digest, and neither does one every waiting
  question was mailed to in the day before the run (`DIGEST_WINDOW`).

A question answered, withdrawn or no longer readable to a person when their mail is
sent is not mailed to them.

## Who is mailed

A question's mail goes to its deciders, `Decision.objects.deciders()`: the active
people who may both answer it (`act`) and read it. The administrator arm and every
owner's `owner_eligible` arm count; a requester is mailed only when another arm lets
them decide; someone who holds `act` without `read` (an owner arm that grants
answering alone) is not mailed, since the mail would name a question whose page they
cannot open. Service users and inactive accounts are left out. By ruling, the
administrator arm is in, and `deciders` has no "assigned to me or unassigned" clause,
so administrators get the asked and reminder mails of questions assigned to others.
The digest lists what waits on a person (`waiting_on`, which has that clause), so a
question assigned to others is not in an administrator's digest.

## What each person reads

Each mail is written from values read for that one person, in their own scope:
`Decision.concern_labels(person)` names the concerned records they read (the first
`QUESTION_RECORD_LIMIT`, with a count of the rest), and
`Decision.requester_name(person)` who asked, so a mail never shows a label or a name
the person could not read in the app, even after their read was revoked. This holds
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

The button links the question's page in the app: `ANGEE_PUBLIC_ORIGIN` joined with
`Decision.get_absolute_url()`. The person signs in if needed and decides there; no
link in a mail decides anything, and none carries a token. The values a mail is
written from are kept with its message, which administrators read, so they hold
nothing secret.

## Mailed once

`DecisionNotice` records each person a question was mailed to, once per occasion
(asked, reminder), and `DigestNotice` each person mailed a digest, once per UTC day;
both share `MailNotice`, whose manager writes a notice once
(`MailNoticeManager.record`).
Each person is mailed in a durable transaction of their own: the send and its notice
together, never one transaction around many sends, so the mail verbs run outside any
transaction, as their tasks do. A notice that exists means the
mail is not sent again, so a retried task mails nobody twice; when two runs race, the
notice's unique key decides and the loser's mail rolls back with its notice. The
message is messaging's to keep or purge: purging it empties the notice's link and
keeps the notice. The ledgers are internal: no public id, no GraphQL surface and no
REBAC resource type, since nothing outside this addon reads them; who was mailed is
also on messaging's system channel, which administrators read.

## When a mail cannot be sent

- **A person's address is refused** (none, or one messaging refuses before or at the
  send, keyed `to`): that person is skipped and counted in the task's log, and the
  others still go.
- **One person's mail fails** (a deployment's override that refuses to render in their
  language, a value too large for the renderer): it is logged by their primary key
  and the refusal's code and counted, the others still go, and the task fails once
  everyone was tried, so the failure stays visible.
- **The deployment cannot mail at all.** Without `ANGEE_PUBLIC_ORIGIN`, or without a
  shipped row, a task refuses before anyone is mailed (`ImproperlyConfigured`,
  `DoesNotExist`). When system mail is not configured, each person's mail renders and
  its send is refused; it is counted, nobody is mailed and the task then fails. These
  fail their run once, in the worker's log, and are not retried. A system channel
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
