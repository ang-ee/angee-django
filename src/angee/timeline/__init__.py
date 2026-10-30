"""The record timeline: one ordered chronology of what happened to a record.

Addons contribute native source queries through ``ANGEE_TIMELINE_KINDS``.
The read combines their authorized rows with SQL UNION ALL before ordering and
paging. Read-time folding retains readable children when their parent is hidden
or gone. Kinds yield affected record identities for commit-time invalidation;
no event projection or backfill is required. Bodies stay with their owners.
"""
