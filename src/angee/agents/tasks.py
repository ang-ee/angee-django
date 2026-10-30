"""Celery entrypoint for the non-retrying agent chat loop."""

from celery import shared_task

from angee.agents import sessions


@shared_task(name="agents.run_session")
def run_session(session_id: int) -> None:
    """Execute one pending turn through the agents session orchestrator."""

    sessions.run_next_turn(session_id)
