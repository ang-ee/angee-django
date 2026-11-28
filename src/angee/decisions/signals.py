"""Notifications inside the owning transaction; external effects must wait for commit.

``decision_asked`` fires once per admitted question, from ``Decision.objects.ask``, once
its row, assignees and concern links exist and admission has passed, so a refused ask
sends nothing. It fires inside the ask's transaction but after decisions' own system
work has ended: receivers hold only the caller's ambient authority, and a receiver
error rolls the admission back. ``Decision.objects.supersede`` asks the replacement
inside its own ``decisions.supersede`` system context, so there a receiver runs
elevated: a receiver reads what it needs under an authority it names itself, never
the ambient one.
``decision_answered`` fires as the answer or withdrawal is retained.
``decision_reminder_due`` fires once per question, from ``Decision.remind``, after
decisions' own system work has ended: receivers hold only the caller's ambient
authority (none from the reminder sweep), so a receiver that delivers the reminder opens
its own named system context for exactly the reads it needs. A receiver error rolls back
that question's reminder.
"""

from django.dispatch import Signal

decision_asked = Signal()
decision_answered = Signal()
decision_reminder_due = Signal()
