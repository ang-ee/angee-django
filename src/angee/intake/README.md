# Intake

A request's position is its Task's stage; after promotion it follows the project's milestones through `MilestoneWork.active_stage`. Do not derive a status.

- [`Need.objects.file_task`](models.py) owns atomic task filing and fingerprints the full request, including its party; queue, party and task permissions stay with their owners.
- The [setup contributor](models.py) links only unassigned needs after share checks, then delegates to the other project setup owners.
- [`Need.reset_access`](models.py) requires confirmation, revision and share authority; it asks a fresh independent decision while retaining the party and prior answers. Requester access follows the Need owner's admitted account through ReBAC; IAM owns credentials.
- A Need [resource row](resources.py) may carry `access: approve|deny` with `access_by`, the xref of who answered. After the row saves and asks its question, the adapter answers it once through `decide_access` as that user, only while it is open; a later load keeps the stored answer.
- The [Task record contribution](web/src/index.tsx) shows each Need's current access decision as a card with the permitted Need verbs beside its requester. The decision link opens the audit history owned by [Decisions](../decisions/README.md). IAM owns credential issuance separately; deciding or resetting request access does not change a password.
- Both task schema nodes project the first linked Need as `requester { display_name email }`. The name uses the linked party or captured claim; the email is returned only to a task writer. Lists select this field without reading a second collection.
- A channel's `intake_requester_domains` lists the domains whose claimed webform addresses get a passwordless account on capture; `["*"]` covers every domain. The account is not admitted: the access decision still decides what the requester reads.

## Clarifying a request

[`Task.clarify(question, *, to=None, reply_by=None, expected_revision=)`](models.py)
asks a triage task's requester a question and holds the task for the answer. The
question is a comment on the task's chatter, addressed to the requester when their
account may reply there, as an admitted requester may. A requester without an
admitted account also gets it as a reply on their request's source message, when
that message came by email on a channel with an outbound sender; the reviewer then
needs the channel's `reply` permission. A requester who can do neither is refused,
not awaited: approve their access first.
The task then snoozes in its triage stage through work's hold, awaiting the
requester's account when they have one, until `reply_by` when given. `to` names the
requester's party when several filed the task.

A requester's message on a conversation intake already captured (an inbound message
on a task's source thread, from the sender of one of its requests) is that
request's reply, not a new request: it stays on the thread as evidence, files
nothing, and wakes the task when its hold awaits that requester or nobody. Their
chatter reply wakes it through work. Either way work sends `task_woken` with
reason `reply` and the message, and a consumer that reviews requests asks its
review again there. "Clarifying" is that hold, shown as a badge or a saved view,
never a triage stage of its own.

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
