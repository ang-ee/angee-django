"""Decision policy registrations and expiry scheduling defaults."""

SETTINGS = {
    "ANGEE_DECISION_POLICY_CLASSES": {
        "first": "angee.decisions.policies.First",
        "all": "angee.decisions.policies.All",
    },
    "ANGEE_DECISION_MAX_ATTEMPTS": 3,
    "CELERY_BEAT_SCHEDULE:append": {
        "decisions.expire": {"task": "decisions.expire", "schedule": 15.0, "options": {"expires": 45}},
    },
}
