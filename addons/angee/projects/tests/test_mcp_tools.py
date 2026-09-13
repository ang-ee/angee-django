"""MCP tool contract for projects, compiled against the real composed schema.

The task tools in :mod:`angee.projects.mcp_tools` compile against the discovery ``public``
schema, so an unknown field, a missing argument or a renamed operation fails here rather
than at an agent's first call. The composed host builds every installed addon, so the
schema is the one an agent actually reaches.
"""

from __future__ import annotations

import asyncio
from typing import Any

from django.apps import apps
from django.test import SimpleTestCase
from fastmcp import FastMCP

from angee.mcp.graphql import DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT
from angee.mcp.server import tool_registrar
from angee.projects import mcp_tools

_EXPECTED_TOOLS = {
    "list_tasks",
    "read_task",
    "create_task",
    "update_task",
    "complete_task",
    "reopen_task",
    "drop_task",
}
_READ_ONLY_TOOLS = {"list_tasks", "read_task"}


def _registered_tools() -> dict[str, Any]:
    """Compile and register every projects spec, returning the tool-name → tool map."""

    server = FastMCP(name="test-projects")
    mcp_tools.register(server)
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


class ProjectsMCPToolTests(SimpleTestCase):
    """The task tools compile, advertise their effects, and never delete a task."""

    tools: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Compile the specs once; each test reads the same registered tools."""

        super().setUpClass()
        cls.tools = _registered_tools()

    def test_all_tools_compile_and_register(self) -> None:
        """Every declared task spec compiles against the schema and registers under its name."""

        self.assertEqual(set(self.tools), _EXPECTED_TOOLS)

    def test_only_the_reads_are_marked_read_only(self) -> None:
        """Queries advertise ``readOnlyHint``; every mutation tool advertises that it writes."""

        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                self.assertIsNotNone(tool.annotations)
                self.assertIs(tool.annotations.readOnlyHint, name in _READ_ONLY_TOOLS)

    def test_no_tool_deletes_a_task(self) -> None:
        """Closing is complete or drop, both reversible, so no tool reaches a delete mutation."""

        self.assertFalse([name for name, tool in self.tools.items() if "delete_" in tool.document])

    def test_only_create_task_requires_a_user_actor(self) -> None:
        """A task's owner is its created_by user FK, so only creation needs a user caller."""

        self.assertEqual({name for name, tool in self.tools.items() if tool.requires_user_actor}, {"create_task"})

    def test_create_task_requires_only_a_title(self) -> None:
        """A task needs a title; project, parent and every other field are optional."""

        self.assertEqual(self.tools["create_task"].parameters["required"], ["title"])

    def test_list_tasks_searches_titles(self) -> None:
        """list_tasks takes an optional title search and a row limit, and projects rows under result."""

        tool = self.tools["list_tasks"]

        self.assertEqual(set(tool.parameters["properties"]), {"limit", "search"})
        self.assertEqual(tool.parameters["required"], [])
        self.assertEqual(tool.search_fields, ("title",))
        self.assertEqual(set(tool.output_schema["properties"]), {"result"})

    def test_update_task_leaves_status_to_the_lifecycle_tools(self) -> None:
        """Status is not a settable field; it moves only through complete, reopen and drop."""

        tool = self.tools["update_task"]

        self.assertNotIn("status", tool.parameters["properties"])
        self.assertEqual(tool.parameters["required"], ["sqid"])

    def test_drop_task_advertises_its_reasons(self) -> None:
        """drop_task requires a reason and advertises the closed set it accepts."""

        tool = self.tools["drop_task"]

        self.assertEqual(tool.parameters["required"], ["sqid", "reason"])
        self.assertEqual(tool.parameters["properties"]["reason"]["enum"], ["DUPLICATE", "DECLINED", "OBSOLETE"])

    def test_list_tasks_is_bounded(self) -> None:
        """An omitted limit takes the reader default, and the reader maximum caps a larger one."""

        tool = self.tools["list_tasks"]

        self.assertEqual((tool.default_limit, tool.max_limit), (DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT))

    def test_appconfig_wires_the_registrar(self) -> None:
        """The MCP owner loads projects' conventional registrar from its native config."""

        self.assertIs(tool_registrar(apps.get_app_config("projects")), mcp_tools.register)
