# Intake

- [`Need.objects.file_task`](models.py) owns atomic task filing and fingerprints the full request, including its party; queue, party and task permissions stay with their owners.
- The [setup contributor](models.py) links only unassigned needs after share checks, then delegates to the other project setup owners.
- [`Need.reset_access`](models.py) requires confirmation, revision and share authority; it asks a fresh independent decision while retaining the party and prior answers. Requester access follows the Need owner's admitted account through ReBAC; IAM owns credentials.
- The [Task record contribution](web/src/index.tsx) shows each Need's current access decision as a card with the permitted Need verbs beside its requester. The decision link opens the audit history owned by [Decisions](../decisions/README.md). IAM owns credential issuance separately; deciding or resetting request access does not change a password.
- Both task schema nodes project the first linked Need as `requester { display_name email }`. The name uses the linked party or captured claim; the email is returned only to a task writer. Lists select this field without reading a second collection.

Access questions assign the target's current sharers when asked. Later grants do
not rewrite that assignment. The Need owner consumes its current answer after
commit through `decide_access`, rechecking normal share and account authority; the
Decision owner only records chosen keys. The nullable `admitted_user` records
the account granted access after successful owner application; a verdict alone
grants no access. Reset or identity replacement clears that receipt. Failed application can be retried through
the Need verb. Captured messaging webforms keep their own input schema.
