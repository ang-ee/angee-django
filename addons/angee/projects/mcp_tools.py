"""Task writes and lifecycle verbs through the actor-scoped GraphQL compiler."""

from fastmcp import FastMCP

from angee.mcp.graphql import ACTION_RESULT, GraphQLTool, register_graphql_tools

_TASK_RESULT = ("sqid", "title", "status", "revision")


def register(server: FastMCP) -> None:
    """Compose task CRUD writes and the same lifecycle actions as the console."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="insert_project_tasks_one",
                name="create_task",
                fields=_TASK_RESULT,
                flatten="object",
                args=("client_creation_key",),
                description="Create a task. Reuse client_creation_key to replay the same creation safely.",
            ),
            GraphQLTool(
                operation="update_project_tasks_by_pk",
                name="update_task",
                fields=_TASK_RESULT,
                id_arg="pk_columns",
                flatten="_set",
                args=("expected_revision",),
                description="Update a writable task by public id. Pass its observed revision to detect stale edits.",
            ),
            GraphQLTool(
                operation="complete_task",
                name="complete_task",
                fields=ACTION_RESULT,
                id_arg="id",
                description="Complete a writable task.",
            ),
            GraphQLTool(
                operation="reopen_task",
                name="reopen_task",
                fields=ACTION_RESULT,
                id_arg="id",
                description="Reopen a writable completed or dropped task.",
            ),
            GraphQLTool(
                operation="drop_task",
                name="drop_task",
                fields=ACTION_RESULT,
                id_arg="id",
                args=("reason",),
                description="Drop a writable task for the selected reason.",
            ),
        ],
    )
