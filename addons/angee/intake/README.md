# Intake

- [`Need.objects.file_task`](models.py) owns atomic task filing and fingerprints the full request, including its party; queue, party and task permissions stay with their owners.
- The [setup contributor](models.py) links only unassigned needs after share checks, then delegates to the other project setup owners.
- [`Need.reset_access`](models.py) requires confirmation, revision and share authority; it creates a pending successor while retaining the party and decision history. Requester access follows the completed decision through ReBAC; IAM owns credentials.
- The [Task record contribution](web/src/index.tsx) shows each Need's current access decision as a card with the permitted Need verbs beside its requester. The decision link opens the audit history owned by [Decisions](../decisions/README.md). IAM owns credential issuance separately; deciding or resetting request access does not change a password.
