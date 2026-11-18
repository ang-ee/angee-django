"""The calendar's model-independent choices, shared by its models and its source backends."""

from django.db import models


class OverlapPolicy(models.TextChoices):
    """Whether a calendar takes events whose busy time meets another's."""

    ACCEPT = "accept", "Accept overlaps"
    REFUSE = "refuse", "Refuse overlaps"


class EventStatus(models.TextChoices):
    """An event's state (RFC 5545 ``STATUS``); a cancelled event shows no occurrence."""

    CONFIRMED = "confirmed", "Confirmed"
    TENTATIVE = "tentative", "Tentative"
    CANCELLED = "cancelled", "Cancelled"


class EventTransparency(models.TextChoices):
    """Whether an event takes its time (RFC 5545 ``TRANSP``); a free event never conflicts."""

    BUSY = "busy", "Busy"
    FREE = "free", "Free"


class EventVisibility(models.TextChoices):
    """Who reads an event's details, as Google Calendar's event ``visibility`` means it.

    ``default`` follows the calendar's readers, ``public`` also shows the details
    to free/busy viewers, and ``private`` shows them only to the calendar's
    writers; everyone else who sees the calendar's busy times sees the event as
    busy time only. An override created at ``default`` takes its master's.
    """

    DEFAULT = "default", "Default"
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"


class AttendeeRole(models.TextChoices):
    """An attendee's part in an event (RFC 5545 ``ROLE``, with the organizer as a role)."""

    ORGANIZER = "organizer", "Organizer"
    REQUIRED = "required", "Required"
    OPTIONAL = "optional", "Optional"


class AttendeeResponse(models.TextChoices):
    """An attendee's RSVP to an event invitation (RFC 5545 ``PARTSTAT`` vocabulary)."""

    NEEDS_ACTION = "needs_action", "Needs Action"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    TENTATIVE = "tentative", "Tentative"
