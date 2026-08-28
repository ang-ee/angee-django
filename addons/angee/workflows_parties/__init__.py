"""Parties governance workflows — human-gated batch operations over contacts.

The dedupe workflow is the first: scan the directory for deterministic
duplicate pairs (shared normalized handle values), suspend one rows-table
Decision in the workflows inbox where a human edits the batch (survivor per
pair, merge / skip / keep-separate), then apply the approved verbs through the
parties manager owners (``PartyManager.merge`` / ``MergeVetoManager.veto``).
The addon owns only workflow step implementations and the seeded graph —
every rule lives on the parties models/managers it composes.
"""
