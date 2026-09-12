"""Match navigation over a readable full transcript, independent of its feed window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from angee.messaging.inbox import MessageInbox
from angee.messaging.managers import _MESSAGE_ORDER


@dataclass(frozen=True)
class InboxTranscriptMatches:
    """Bounded visible matches and nearest match anchors; totals cover the full thread."""

    thread: Any
    count: int
    total: int
    visible: list[str]
    previous: str | None
    next: str | None


class InboxTranscript:
    """The transcript stays complete; only matching flags and navigation use the result predicates."""

    def __init__(self, inbox: MessageInbox, matching: Any, thread: str) -> None:
        self.inbox = inbox
        self.thread = inbox.threads.from_public_id(thread)
        if self.thread is None:
            raise ValueError("Conversation unavailable.")
        self.messages = inbox.collection("messaging", "Message").filter(thread=self.thread).for_feed()
        self.matching = matching.filter(thread=self.thread)

    def matches(self, *, visible: list[str], at: str = "") -> InboxTranscriptMatches:
        """Annotate one loaded window and navigate by the feed's canonical chronological order."""

        if len(visible) > 1000:
            raise ValueError("A transcript match window cannot exceed 1000 messages.")
        anchor = self.messages.from_public_id(at) if at else None
        if at and anchor is None:
            raise ValueError("Message unavailable in this conversation.")
        before, after = self.matching, self.matching
        if anchor is not None:
            before = before.filter(_MESSAGE_ORDER.before(anchor.chronological_key))
            after = after.filter(_MESSAGE_ORDER.after(anchor.chronological_key))
        previous = before.order_by("-_order_at", "-pk").first()
        following = after.order_by("_order_at", "pk").first()
        return InboxTranscriptMatches(
            thread=self.thread,
            count=self.matching.count(),
            total=self.messages.count(),
            visible=[message.public_id for message in self.matching.filter(sqid__in=visible)],
            previous=previous.public_id if previous else None,
            next=following.public_id if following else None,
        )
