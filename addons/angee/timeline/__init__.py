"""The record timeline: one ordered chronology of what happened to a record.

Addons contribute one-line events through the ``ANGEE_TIMELINE_KINDS`` hook;
the ``timeline`` read unions them behind one cursor. Bodies stay with their owners.
"""
