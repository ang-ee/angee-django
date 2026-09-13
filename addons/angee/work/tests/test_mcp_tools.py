"""MCP tool contract for work, compiled against the real composed schema.

The queue, triage and cycle tools in :mod:`angee.work.mcp_tools` compile against the
discovery ``public`` schema, where work's task extension sits on the projects task node,
so a drift between a tool spec and the schema fails here rather than at an agent's first
call.
"""

from __future__ import annotations

import asyncio
from types import ModuleType
from typing import Any

from django.apps import apps
from django.test import SimpleTestCase
from fastmcp import FastMCP

from angee.mcp.graphql import ACTION_RESULT, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT
from angee.mcp.server import tool_registrar
from angee.projects import mcp_tools as projects_mcp_tools
from angee.work import mcp_tools

_EXPECTED_TOOLS = {
    "list_queues",
    "list_stages",
    "list_cycles",
    "read_task_work",
    "accept_task",
    "decline_task",
    "snooze_task",
    "mark_task_duplicate",
    "close_cycle",
}
_READ_ONLY_TOOLS = {"list_queues", "list_stages", "list_cycles", "read_task_work"}


def _registered_tools(module: ModuleType) -> dict[str, Any]:
    """Compile and register one addon's specs, returning the tool-name → tool map."""

    server = FastMCP(name=f"test-{module.__name__}")
    module.register(server)
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


class WorkMCPToolTests(SimpleTestCase):
    """The work tools compile, take their target as a sqid, and never delete configuration."""

    tools: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Compile the specs once; each test reads the same registered tools."""

        super().setUpClass()
        cls.tools = _registered_tools(mcp_tools)

    def test_all_tools_compile_and_register(self) -> None:
        """Every declared work spec compiles against the composed schema and registers."""

        self.assertEqual(set(self.tools), _EXPECTED_TOOLS)

    def test_only_the_reads_are_marked_read_only(self) -> None:
        """Queries advertise ``readOnlyHint``; every mutation tool advertises that it writes."""

        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                self.assertIsNotNone(tool.annotations)
                self.assertIs(tool.annotations.readOnlyHint, name in _READ_ONLY_TOOLS)

    def test_no_tool_deletes_anything(self) -> None:
        """Queues, stages and cycles are configuration; no agent tool reaches a delete mutation."""

        self.assertFalse([name for name, tool in self.tools.items() if "delete_" in tool.document])

    def test_read_task_work_projects_the_key_and_work_state(self) -> None:
        """read_task_work returns the queue-keyed number beside the queue, stage and cycle."""

        tool = self.tools["read_task_work"]

        self.assertEqual(tool.parameters["required"], ["sqid"])
        self.assertLessEqual({"work_key", "queue", "stage", "cycle", "estimate"}, set(tool.output_schema["properties"]))
        self.assertEqual(set(tool.output_schema["properties"]["stage"]["properties"]), {"sqid", "name", "category"})

    def test_action_tools_take_the_target_as_sqid(self) -> None:
        """Each action names its target ``sqid``, requires exactly its own arguments, and reports."""

        for name, required in (
            ("accept_task", ["sqid"]),
            ("decline_task", ["sqid", "reason"]),
            ("snooze_task", ["sqid", "until"]),
            ("mark_task_duplicate", ["sqid", "canonical"]),
            ("close_cycle", ["sqid"]),
        ):
            with self.subTest(tool=name):
                tool = self.tools[name]
                self.assertEqual(tool.parameters["required"], required)
                self.assertEqual(set(tool.output_schema["properties"]), set(ACTION_RESULT))

    def test_accept_task_stage_is_optional(self) -> None:
        """Accepting without a stage lands the task in its queue's default stage."""

        tool = self.tools["accept_task"]

        self.assertIn("stage", tool.parameters["properties"])
        self.assertNotIn("stage", tool.parameters["required"])

    def test_decline_task_advertises_its_reasons(self) -> None:
        """decline_task advertises the same closed reasons a dropped task records."""

        reasons = self.tools["decline_task"].parameters["properties"]["reason"]["enum"]

        self.assertEqual(reasons, ["DUPLICATE", "DECLINED", "OBSOLETE"])

    def test_update_task_moves_work_state(self) -> None:
        """The module docstring's promise: projects' update_task carries queue, stage and cycle."""

        tool = _registered_tools(projects_mcp_tools)["update_task"]

        self.assertLessEqual({"queue", "stage", "cycle", "estimate"}, set(tool.parameters["properties"]))

    def test_list_tools_are_bounded(self) -> None:
        """Each list takes the reader default when no limit is given and caps a larger one."""

        for name in ("list_queues", "list_stages", "list_cycles"):
            with self.subTest(tool=name):
                tool = self.tools[name]
                self.assertEqual((tool.default_limit, tool.max_limit), (DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT))

    def test_appconfig_wires_the_registrar(self) -> None:
        """The MCP owner loads work's conventional registrar from its native config."""

        self.assertIs(tool_registrar(apps.get_app_config("work")), mcp_tools.register)
