"""Give MCP tools their identity and retire evidenced selection mirrors."""

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import SqidField


def _legacy_id(value, *, prefix):
    """Encode one historical public id through its owning field implementation."""

    return SqidField(real_field_name="id", prefix=prefix, min_length=8).public_id_from_value(value)


def applies(project_state: ProjectState) -> bool:
    """Apply only to the exact pre-grant-id MCP catalogue state."""

    tool = project_state.models.get(("agents", "mcptool"))
    agent = project_state.models.get(("agents", "agent"))
    server = project_state.models.get(("agents", "mcpserver"))
    if tool is None and agent is None and server is None:
        return False
    if tool is None or agent is None or server is None:
        raise ImproperlyConfigured(
            "angee.agents:live_tool_backing found only part of its MCP model state"
        )
    required_tool = {"server", "name"}
    required_agent = {"user", "mcp_tools", "mcp_servers"}
    if "grant_id" in tool.fields:
        grant_id = tool.fields["grant_id"]
        if (
            isinstance(grant_id, models.CharField)
            and grant_id.max_length == 260
            and grant_id.unique
            and not grant_id.null
            and not grant_id.editable
        ):
            return False
        raise ImproperlyConfigured(
            "angee.agents:live_tool_backing found a partial MCPTool grant-id state"
        )
    if not required_tool.issubset(tool.fields) or not required_agent.issubset(agent.fields):
        raise ImproperlyConfigured(
            "angee.agents:live_tool_backing found an unexpected MCP selection state"
        )
    return True


def _through_field_for(through_model, target_model):
    """Return the through-table FK whose remote model is ``target_model``."""

    return next(
        field
        for field in through_model._meta.fields
        if getattr(getattr(field, "remote_field", None), "model", None) is target_model
    )


def _delete_tuple(rows, *, registry=False, **identity):
    """Delete one exact uncaveated tuple in either physical storage shape."""

    if registry:
        identity = {
            "resource_fk__resource_type": identity.pop("resource_type"),
            "resource_fk__resource_id": identity.pop("resource_id"),
            "subject_fk__resource_type": identity.pop("subject_type"),
            "subject_fk__resource_id": identity.pop("subject_id"),
            **identity,
        }
    rows.filter(**identity, optional_subject_relation="", caveat_name="").delete()


def populate_grant_ids(apps, schema_editor) -> None:
    """Backfill each tool from its persisted server primary key and tool name."""

    database = schema_editor.connection.alias
    tool_model = apps.get_model("agents", "MCPTool")
    for tool in tool_model._base_manager.using(database).iterator():
        server_id = _legacy_id(tool.server_id, prefix="mcp_")
        tool.grant_id = f"{server_id}.{str(tool.name).strip()}"
        tool.save(update_fields=("grant_id",))


def remove_evidenced_mirrors(apps, schema_editor) -> None:
    """Delete only uncaveated tuples matching a persisted legacy M2M edge."""

    database = schema_editor.connection.alias
    stores = (
        (
            apps.get_model("rebac", "Relationship")._base_manager.using(database),
            False,
        ),
        (
            apps.get_model("rebac", "RelationshipRegistry")._base_manager.using(database),
            True,
        ),
    )
    agent = apps.get_model("agents", "Agent")

    tool_through = agent.mcp_tools.through
    tool = apps.get_model("agents", "MCPTool")
    tool_agent_field = _through_field_for(tool_through, agent)
    tool_field = _through_field_for(tool_through, tool)
    selections = tool_through._base_manager.using(database).values_list(
        f"{tool_agent_field.name}__user_id",
        f"{tool_field.name}__grant_id",
    )
    for user_id, grant_id in selections.iterator():
        if user_id is None:
            continue
        for rows, is_registry in stores:
            _delete_tuple(
                rows,
                registry=is_registry,
                resource_type="agents/tool_grant",
                resource_id=str(grant_id),
                relation="grantee",
                subject_type="auth/user",
                subject_id=_legacy_id(user_id, prefix="usr_"),
            )

    server_through = agent.mcp_servers.through
    server = apps.get_model("agents", "MCPServer")
    server_agent_field = _through_field_for(server_through, agent)
    server_field = _through_field_for(server_through, server)
    server_selections = server_through._base_manager.using(database).values_list(
        f"{server_agent_field.name}_id",
        f"{server_field.name}_id",
    )
    for agent_id, server_id in server_selections.iterator():
        for rows, is_registry in stores:
            _delete_tuple(
                rows,
                registry=is_registry,
                resource_type="agents/mcp_server",
                resource_id=_legacy_id(server_id, prefix="mcp_"),
                relation="agent",
                subject_type="agents/agent",
                subject_id=_legacy_id(agent_id, prefix="agt_"),
            )


class Migration(migrations.Migration):
    """Adopt persisted MCPTool rows as the canonical tool-grant resources."""

    dependencies = [
        ("agents", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations = [
        migrations.AddField(
            model_name="mcptool",
            name="grant_id",
            field=models.CharField(blank=True, editable=False, max_length=260, null=True),
        ),
        migrations.RunPython(populate_grant_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mcptool",
            name="grant_id",
            field=models.CharField(editable=False, max_length=260, unique=True),
        ),
        migrations.RunPython(remove_evidenced_mirrors, migrations.RunPython.noop),
    ]
