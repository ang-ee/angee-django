"""Agent catalogue — agents and their templates, skills, MCP servers/tools, and
the inference provider/model catalogue they run on.

Composes onto the ``integrate`` seams: skills are discovered through an
``integrate.Source`` (``source_kind="skill"``), and an inference provider is an
``integrate.Capability`` over an ``Integration`` whose credential carries the API
key. Owns no process lifecycle — the operator renders an agent into a workspace and
service; this addon keeps the definitions.
"""
