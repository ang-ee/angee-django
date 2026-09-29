# Intake

[`Need.objects.file_task`](models.py), exposed as `file_task_with_need`, creates
a task and its request evidence in one transaction. The creation key fingerprints
the entire request, including its party; changing the payload conflicts. Queue
and party read, task creation and request-sharing gates remain with their
existing owners.

The project setup contributor links unassigned needs to the selected `submitter`
through normal need saves. Existing party assignments are retained. It delegates
to the remaining setup contributors only after the need's share checks succeed.
