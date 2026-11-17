"""Notifications inside the owning transaction; external effects must wait for commit.

``decision_answered`` fires as the answer or withdrawal is retained.
``decision_reminder_due`` fires once per question, from ``Decision.remind``, after
decisions' own system work has ended: receivers hold only the caller's ambient
authority (none from the reminder sweep), so a receiver that delivers the reminder opens
its own named system context for exactly the reads it needs. A receiver error rolls back
that question's reminder.
"""

from django.dispatch import Signal

decision_answered = Signal()
decision_reminder_due = Signal()
