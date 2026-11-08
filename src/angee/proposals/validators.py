"""Value validation shared by proposal declarations and persisted rows."""


def normalize_topic_key(value: str) -> str:
    """Return the nonempty, case-insensitive stable topic key."""

    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Topic key is required.")
    return normalized
