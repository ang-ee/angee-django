"""Projects tools for the MCP server — curated actor-scoped task operations.

Each tool runs the same public GraphQL operation the browser does, so strawberry's
``RebacManager`` scoping and the Angee write backend do the authorization, and the
:mod:`angee.mcp.graphql` compiler derives each tool's input schema, document and
projection from the schema; this module only declares which operations agents may
drive. Task ids are public ``tsk_`` sqids; fields are snake_case.

A task's owner relation is its ``created_by`` user FK, so ``create_task`` requires a
user actor, as the notes example's create does. Closing a task is ``complete_task``
or ``drop_task`` rather than a delete: both reverse through ``reopen_task``, so an
agent can finish or abandon work without destroying it.
"""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.graphql import ACTION_RESULT, GraphQLTool, register_graphql_tools

_TASK_SUMMARY = (
    "sqid",
    "title",
    "status",
    "priority",
    "due_date",
    ("project", ("sqid", "display_name")),
)
_TASK_DETAIL = (
    *_TASK_SUMMARY,
    "note",
    "assignee",
    "delegate",
    "recurrence",
    "dropped_reason",
    "done_at",
    "dropped_at",
    ("milestone", ("sqid", "display_name")),
    ("parent", ("sqid", "title")),
)


def register(server: FastMCP) -> None:
    """Register the GraphQL-backed task tools on the MCP server."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="project_tasks",
                name="list_tasks",
                fields=_TASK_SUMMARY,
                limit_arg="limit",
                search_fields=("title",),
                description="List tasks the caller can read; search matches text in the title. Returns the "
                "public tsk_ ids the other task tools take.",
            ),
            GraphQLTool(
                operation="project_tasks_by_pk",
                name="read_task",
                fields=_TASK_DETAIL,
                id_arg="id",
                description="Read one task in full by its public tsk_ sqid.",
            ),
            GraphQLTool(
                operation="insert_project_tasks_one",
                name="create_task",
                fields=_TASK_DETAIL,
                flatten="object",
                requires_user_actor=True,
                description="Create a task owned by the user caller and return it. project, milestone and "
                "parent take public prj_, mls_ and tsk_ sqids; priority is none, low, medium, high or urgent.",
            ),
            GraphQLTool(
                operation="update_project_tasks_by_pk",
                name="update_task",
                fields=_TASK_DETAIL,
                id_arg="pk_columns",
                flatten="_set",
                description="Update fields of a task the caller may write, by its public tsk_ sqid, and return "
                "it. Status moves through complete_task, reopen_task and drop_task instead.",
            ),
            GraphQLTool(
                operation="complete_task",
                name="complete_task",
                fields=ACTION_RESULT,
                id_arg="id",
                description="Mark a task the caller may write as done, by its public tsk_ sqid.",
            ),
            GraphQLTool(
                operation="reopen_task",
                name="reopen_task",
                fields=ACTION_RESULT,
                id_arg="id",
                description="Reopen a done or dropped task the caller may write, by its public tsk_ sqid.",
            ),
            GraphQLTool(
                operation="drop_task",
                name="drop_task",
                fields=ACTION_RESULT,
                id_arg="id",
                args=("reason",),
                description="Drop a task the caller may write, by its public tsk_ sqid, for reason DUPLICATE, "
                "DECLINED or OBSOLETE. reopen_task reverses it.",
            ),
        ],
    )
