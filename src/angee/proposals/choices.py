"""Native proposal lifecycle, disclosure choices and audience ordering."""

from django.db import models


class RoundOpeningPolicy(models.TextChoices):
    """Which responder-owned surfaces disclosure opens."""

    FACILITATOR_ONLY = "facilitator_only", "Facilitator only"
    ANSWERS = "answers", "Answers"
    ANSWERS_AND_TRACKS = "answers_and_tracks", "Answers and tracks"
    DRAFTS_AND_TRACKS = "drafts_and_tracks", "Drafts and tracks"


class RoundStatus(models.TextChoices):
    """Code-owned Round lifecycle."""

    COLLECTING = "collecting", "Collecting"
    OPENED = "opened", "Opened"
    CLOSED = "closed", "Closed"
    CANCELED = "canceled", "Canceled"


class RoundOutcome(models.TextChoices):
    """Terminal close outcomes."""

    AWARDED = "awarded", "Awarded"
    NO_AWARD = "no_award", "No award"


class ProposalState(models.TextChoices):
    """Single Proposal document lifecycle."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    ACCEPTED = "accepted", "Accepted"
    PARTIALLY_ACCEPTED = "partially_accepted", "Partially accepted"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ProposalConfidence(models.TextChoices):
    """Closed confidence vocabulary for comparison."""

    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class RosterVisibility(models.TextChoices):
    """Responder roster projection detail."""

    HIDDEN = "hidden", "Hidden"
    NAMED = "named", "Named"
    STATUS = "status", "Status"


class ClarificationAskers(models.TextChoices):
    """Whether peers may read the asker column."""

    HIDDEN = "hidden", "Hidden"
    NAMED = "named", "Named"


class AnswerVisibility(models.TextChoices):
    """Successively narrower inherited answer audiences."""

    ROUND = "round", "Round"
    RESPONDER = "responder", "Responder"
    SEALED = "sealed", "Managers only"


OPENING_POLICY_ORDER = (
    RoundOpeningPolicy.FACILITATOR_ONLY,
    RoundOpeningPolicy.ANSWERS,
    RoundOpeningPolicy.ANSWERS_AND_TRACKS,
    RoundOpeningPolicy.DRAFTS_AND_TRACKS,
)


ANSWER_VISIBILITY_ORDER = (AnswerVisibility.ROUND, AnswerVisibility.RESPONDER, AnswerVisibility.SEALED)


class QuestionAudience(models.TextChoices):
    """Audience choices when asking or narrowing an unpassed question."""

    DEFAULT = "default", "Section default"
    MANAGERS = "managers", "Managers"


class PassAudience(models.TextChoices):
    """Audience choices for a question's first pass."""

    DEFAULT = "default", "Section default"
    ASKER = "asker", "Asker"
