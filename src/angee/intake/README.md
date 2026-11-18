# Intake

A request's position is its Task's stage; after promotion it follows the project's milestones through `MilestoneWork.active_stage`. Do not derive a status.

- [`Need.objects.file_task`](models.py) owns atomic task filing and fingerprints the full request, including its party; queue, party and task permissions stay with their owners.
- The [setup contributor](models.py) links only unassigned needs after share checks, then delegates to the other project setup owners.
- [`Need.reset_access`](models.py) requires confirmation, revision and share authority; it asks a fresh independent decision while retaining the party and prior answers. Requester access follows the Need owner's admitted account through ReBAC; IAM owns credentials.
- A Need [resource row](resources.py) may carry `access: approve|deny` with `access_by`, the xref of who answered. After the row saves and asks its question, the adapter answers it once through `decide_access` as that user, only while it is open; a later load keeps the stored answer.
- The [Task record contribution](web/src/index.tsx) shows each Need's current access decision as a card with the permitted Need verbs beside its requester. The decision link opens the audit history owned by [Decisions](../decisions/README.md). IAM owns credential issuance separately; deciding or resetting request access does not change a password.
- Both task schema nodes project the first linked Need as `requester { display_name email }`. The name uses the linked party or captured claim; the email is returned only to a task writer. Lists select this field without reading a second collection.
- A channel's `intake_requester_domains` lists the domains whose claimed webform addresses get a passwordless account on capture; `["*"]` covers every domain. A claimed address is unverified: with `["*"]`, anyone who submits the form gets an account for whatever address they type. That account has no credential, no admission and no membership, so it reads nothing until the access decision admits it, and the address is only proposed to its party for review, never confirmed. Configure `["*"]` only where such accounts are acceptable; changing the list requires channel write and account-create permission.

## Clarifying a request

[`Task.clarify(question, *, to=None, reply_by=None, expected_revision=)`](models.py)
asks a triage task's requester a question and holds the task for the answer. It
locks the task first, under the same write and revision check as accept and
decline. `to` names the request to ask about when several requesters filed the
task. A requester without an account gets a passwordless one through the same
linking the `["*"]` policy uses (`Need.provide_requester_account`): no credential,
no admission, no membership. The task then snoozes in its triage stage through
work's hold, awaiting that account, until `reply_by` when given; a hold never
awaits the asker.

The question is a comment on the task's chatter, addressed to the requester when
their account may reply there, as an admitted requester may. A requester without
an admitted account also gets it by email when their request came as an email
with a sender, on a channel with an outbound sender: messaging's `compose_reply`
addresses, titles and threads the reply and queues its delivery, and the
reviewer needs the channel's `reply` permission. A requester reachable neither
way is refused, not awaited: approve their access first.

One rule decides which message wakes the hold, and work owns it
(`TaskWorkQuerySet.wake_replied`): only the awaited account's reply. On chatter
that is a message the account posts. By email it is an inbound message that
answers a message on the request's own conversation (its `In-Reply-To` resolved
there), while the task is still in triage, from the request's sender or from an
address the request's `Reply-To` named, where a relayed requester (a web form, a
mailing list) answers; no other sender on the thread is the requester. Intake
takes it as that request's reply, files nothing new and names the account to
work. For a relayed request the requester's address, and so their account, is
the `Reply-To` one, never the relay's. A fresh email that only reuses a subject, or any email after the review
ended, is a new request. Work sends `task_woken` with reason `reply` and the
message, and a consumer that reviews requests asks its review again there. A
hold awaiting nobody wakes only at its date or by hand. "Clarifying" is a hold
awaiting a requester, shown as a badge or a saved view, never a triage stage of
its own.

Access questions assign the target's current sharers when asked. Later grants do
not rewrite that assignment. The Need owner consumes its current answer synchronously inside the answer
transaction, rechecking normal share and account authority; the Decision owner
records chosen keys and emits the answer signal. The nullable `admitted_user` records
the account granted access after successful owner application; a verdict alone
grants no access. Reset or identity replacement clears that receipt. Failed application rolls back the verdict and owner changes together. Replacing
an unanswered question withdraws it; answered questions remain audit history. Captured messaging webforms keep their own input schema.

Task merges move Needs through their model-owned reference contract and normal
assignment checks, inside the shared merge changeset. Need history retains every
previous target; the changeset records the cause of each write.
