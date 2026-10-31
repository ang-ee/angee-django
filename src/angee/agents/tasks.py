"""Celery entrypoint for the non-retrying agent chat loop."""

from celery import shared_task

from angee.agents import sessions


@shared_task(name="agents.run_session")
def run_session(session_id: int, *, using: str | None = None) -> None:
    """Execute on the producer alias, or the native database resolver when omitted."""

    sessions.run_next_turn(session_id, using=using)
