"""Retired fold body; see the package docstring."""


def plan(*args, **kwargs):
    """Refuse to replay a fold that every stack applied before this release."""

    raise RuntimeError(
        "The parties fold migrations are retired history; collapse the stack's migration history "
        "(reset_migration_history) instead of replaying them."
    )
