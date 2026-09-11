"""Work tools for the MCP server — curated actor-scoped queue, triage and cycle operations.

``angee.work`` extends ``projects.Task`` with a queue, stage, cycle, estimate and a
queue-keyed number, so these tools read and drive that work state while
:mod:`angee.projects` owns the task itself. Each tool runs the same public GraphQL
operation the browser does, so ``RebacManager`` scoping and the write backend do the
authorization, and :mod:`angee.mcp.graphql` derives each tool's schema, document and
projection. Queue ids are ``grp_``, stage ids ``stg_``, cycle ids ``cyc_`` and task
ids ``tsk_`` public sqids.

Moving a task between queues, stages or cycles is an ordinary field write, so it goes
through the projects ``update_task`` tool with a ``grp_``, ``stg_`` or ``cyc_`` id.
"""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.graphql import ACTION_RESULT, GraphQLTool, register_graphql_tools

_QUEUE = ("sqid", "key", "name")


def register(server: FastMCP) -> None:
    """Register the GraphQL-backed work tools on the MCP server."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="work_queues",
                name="list_queues",
                fields=(*_QUEUE, "triage_enabled", "cycles_enabled"),
                limit_arg="limit",
                description="List work queues the caller can read. A queue's key prefixes its task numbers, "
                "as in ENG-42.",
            ),
            GraphQLTool(
                operation="work_stages",
                name="list_stages",
                fields=("sqid", "name", "category", "position", ("queue", ("sqid", "key"))),
                limit_arg="limit",
                description="List workflow stages with their category: TRIAGE, BACKLOG, UNSTARTED, STARTED, "
                "COMPLETED, CANCELED or DUPLICATE. Pass a stg_ id to update_task to move a task.",
            ),
            GraphQLTool(
                operation="work_cycles",
                name="list_cycles",
                fields=("sqid", "name", "number", "starts_on", "ends_on", "completed_at", ("queue", ("sqid", "key"))),
                limit_arg="limit",
                description="List cycles the caller can read, with their dates and completion.",
            ),
            GraphQLTool(
                operation="project_tasks_by_pk",
                name="read_task_work",
                fields=(
                    "sqid",
                    "work_key",
                    "title",
                    "estimate",
                    "snoozed_until",
                    ("queue", _QUEUE),
                    ("stage", ("sqid", "name", "category")),
                    ("cycle", ("sqid", "name", "number")),
                ),
                id_arg="id",
                description="Complement read_task with a task's work state, by its public tsk_ sqid: its key "
                "such as ENG-42, queue, stage, cycle, estimate and snooze.",
            ),
            GraphQLTool(
                operation="accept_task",
                name="accept_task",
                fields=ACTION_RESULT,
                id_arg="task",
                args=("stage",),
                description="Accept a triage task, by its public tsk_ sqid, into stage (a stg_ id), or into its "
                "queue's default stage when stage is omitted.",
            ),
            GraphQLTool(
                operation="decline_task",
                name="decline_task",
                fields=ACTION_RESULT,
                id_arg="task",
                args=("reason",),
                description="Decline a triage task, by its public tsk_ sqid, for reason DUPLICATE, DECLINED or "
                "OBSOLETE.",
            ),
            GraphQLTool(
                operation="snooze_task",
                name="snooze_task",
                fields=ACTION_RESULT,
                id_arg="task",
                args=("until",),
                description="Snooze a triage task, by its public tsk_ sqid, until an ISO 8601 date-time; new "
                "chatter on the task wakes it early.",
            ),
            GraphQLTool(
                operation="mark_task_duplicate",
                name="mark_task_duplicate",
                fields=ACTION_RESULT,
                id_arg="task",
                args=("canonical",),
                description="Merge a task, by its public tsk_ sqid, into canonical (another tsk_ sqid) as its "
                "duplicate.",
            ),
            GraphQLTool(
                operation="close_work_cycle",
                name="close_cycle",
                fields=ACTION_RESULT,
                id_arg="cycle",
                description="Close a cycle, by its public cyc_ sqid, rolling its unfinished tasks over.",
            ),
        ],
    )
