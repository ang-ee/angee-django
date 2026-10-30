# Workflow pitfalls

Moved from the backend guide. The execution contracts stayed there:
[Workflow contracts](../../../../../../docs/backend/guidelines.md#workflow-contracts).

- **Conditional queryset updates send no model signals.** Workflow owners
  publish completed run writes through `publish_change` and step transitions
  through bounded `bulk_updated` batches. Dispatch locks ready
  rows with `skip_locked` and uses `enqueue_task` to send after commit; callers
  do not infer publication or task delivery from `post_save`.
