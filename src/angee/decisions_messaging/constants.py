"""Names the decision mail addon shares between its modules."""

MAIL_DECISION_TASK = "decisions_messaging.mail_decision"
MAIL_DIGESTS_TASK = "decisions_messaging.mail_digests"

DIGEST_HOUR = 7
"""The hour of the day, in Celery's time zone (UTC, the jobs addon's ``CELERY_TIMEZONE``), the daily digest is sent."""
