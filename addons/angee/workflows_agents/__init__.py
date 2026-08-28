"""Composition addon connecting workflow activity steps to the agents catalogue.

This addon owns both the one-shot ``agent`` activity and the durable
``agent_session`` activity, plus the session service and owner-gated mutations
that bind the agents catalogue to workflow execution.
"""
