"""Expose curated GraphQL operations as MCP tools, scoped to the request actor.

The reuse seam for the MCP tool layer: instead of re-deriving CRUD, projection, and
permission gating per model, an MCP tool runs the *same* GraphQL operation a browser
would, under the agent's REBAC actor — so strawberry's own ``permission_classes`` and
``RebacManager`` scoping do the authorization.

An addon declares a :class:`GraphQLTool` per operation and calls :func:`register`; the
compiler introspects the named schema bucket (graphql-core) to derive the tool's input
schema, response projection, and operation document, then registers a FastMCP tool whose
:meth:`_CompiledTool.run` executes the operation through :func:`execute_under_actor`.

Boundary conventions (the agent surface differs from the GraphQL wire):
- ids are the public ``sqid``; a GraphQL ``id`` arg/field is exposed as ``sqid``.
- field names are ``snake_case`` for the agent; the compiler uses the schema's
  actual wire name, whether camelCase or Hasura snake_case.
- a single input object (``createNote(data:)`` / ``insert_notes_one(object:)``)
  is flattened to top-level tool args.
- an offset-paginated list exposes ``limit`` → ``pagination.limit`` and projects
  ``results``; a Hasura list uses top-level ``limit`` and returns rows directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import reversion
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLScalarType,
    Undefined,
)
from pydantic import BaseModel
from rebac import current_actor, system_context
from strawberry.utils.str_converters import to_camel_case, to_snake_case

from angee.base.actors import actor_user_id, is_user_actor
from angee.graphql.schema import GraphQLSchemas
from mcp.types import ToolAnnotations

_SCALAR_JSON = {
    "Int": "integer",
    "Float": "number",
    "Boolean": "boolean",
    "ID": "string",
    "String": "string",
    "DateTime": "string",
}
"""GraphQL scalar name → JSON Schema ``type``. ``JSON`` maps to an unconstrained schema."""


async def execute_under_actor(schema: str, document: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute ``document`` against the named schema bucket under the ambient actor.

    Returns the operation's ``data``. Raises the first GraphQL error so the MCP layer
    surfaces it as a tool error rather than returning a partial result. The actor is
    whatever ``rebac.current_actor()`` holds — set per call by
    :class:`angee.mcp.middleware.ActorMiddleware`; the schema's ``RebacExtension`` opens
    its own scopes and pins every queryset to that actor, so no Django request is needed.

    Runs ``execute_sync`` in a thread: it matches the sync Django GraphQL view the browser
    uses, and the crud delete resolver does sync ORM that isn't async-safe. ``sync_to_async``
    (thread-sensitive) copies the ambient actor ContextVar into the sync thread.
    """

    built = GraphQLSchemas.from_discovery().build(schema)
    result = await sync_to_async(_execute_sync)(built, document, variables)
    if result.errors:
        raise _tool_error(result.errors[0])
    return dict(result.data or {})


def _execute_sync(built: Any, document: str, variables: dict[str, Any] | None) -> Any:
    """Run the GraphQL operation synchronously, recycling stale DB connections.

    The MCP path has no Django request, so the ``request_started``/``request_finished``
    signals that close old connections never fire. Bracket the execution with
    ``close_old_connections()`` so the long-lived ``sync_to_async`` worker thread honours
    ``CONN_MAX_AGE``/``CONN_HEALTH_CHECKS`` like a request does — otherwise a DB restart or
    idle timeout surfaces as a stale-connection error on the next call instead of a
    transparent reconnect.

    The same missing request also means ``reversion.middleware.RevisionMiddleware`` never
    runs, so a registered model saved here (an agent's body edit) would persist with no
    ``Version``. Open the revision context the middleware would and bind the ambient actor
    as its user (mirroring the middleware's ``request.user`` binding). A read records
    nothing — no registered model saves, so reversion writes no revision — so wrapping
    every operation is safe.
    """

    close_old_connections()
    try:
        with reversion.create_revision():
            result = built.execute_sync(
                document,
                variable_values=dict(variables or {}),
                context_value=SimpleNamespace(request=None),
            )
            reversion.set_user(_revision_user())
            return result
    finally:
        close_old_connections()


def _revision_user() -> Any | None:
    """Return the Django user behind the ambient MCP actor for revision attribution.

    The MCP analog of ``RevisionMiddleware``'s ``request.user`` binding: resolve rebac's
    ambient actor (entered per call by :class:`~angee.mcp.middleware.ActorMiddleware` and
    copied into this sync worker thread) to its user row. Returns ``None`` for an actor
    that is not a user, so the revision stays unattributed exactly as the middleware
    leaves an anonymous request. Read under ``system_context`` so resolving the attributor
    never pulls a REBAC-guarded user row into the actor's own scope.
    """

    user_id = actor_user_id(current_actor())
    if user_id is None:
        return None
    with system_context(reason="mcp.graphql.revision_user"):
        return get_user_model()._default_manager.filter(pk=user_id).first()


def _tool_error(error: Any) -> ToolError:
    """Translate a GraphQL error into an agent-safe ``ToolError`` (no internals leaked).

    ``AngeeSchema.process_errors`` has already classified rebac denials and Django
    validation onto ``error.extensions``; reuse that instead of forwarding the raw
    message, which can carry ORM/model internals. FastMCP masks every *other* exception
    (``mask_error_details``), so only this curated text reaches the agent.
    """

    extensions = error.extensions or {}
    if extensions.get("code") == "PERMISSION_DENIED":
        return ToolError("You do not have permission to perform this operation.")
    if extensions.get("code") == "UNAUTHENTICATED":
        return ToolError("This operation requires an authenticated actor.")
    if extensions.get("validationErrors"):
        return ToolError(f"Invalid input: {extensions['validationErrors']}.")
    return ToolError("The operation could not be completed.")


type ProjectionSpec = str | tuple[str, tuple["ProjectionSpec", ...]]
"""One ``fields`` entry: a scalar leaf name, or ``(object_name, (child, ...))`` branch.

A branch projects a nested object or list of objects by naming its own children; the
children may themselves be branches up to a total nesting depth of two
(``markdown { body outline { level } }``). ``sqid`` selects a node's public id at any
level (the GraphQL ``id`` field). Deeper nesting fails fast at compile time.
"""


@dataclass(frozen=True)
class GraphQLTool:
    """Declaration of one MCP tool backed by a GraphQL operation.

    ``operation`` is the root field name in the ``schema`` bucket; ``name`` is the MCP
    tool name. ``fields`` is the response projection in snake_case (``sqid`` selects the
    node's public id). Each entry is a scalar leaf or a ``(name, (child, ...))`` branch
    that projects a nested object/list one or two levels deep (see
    :data:`ProjectionSpec`). The compiler derives the input schema and document
    from introspection; the hints below name the input args the tool drives:
    ``flatten`` lifts an input object's fields to top-level args, ``id_arg`` exposes a
        scalar GraphQL id arg as ``sqid``, ``limit_arg`` maps a top-level int to
    ``pagination.limit`` for an offset-paginated list, ``args`` passes named root
    arguments straight through as top-level tool inputs (scalars, enums, or lists
    thereof — for operations whose inputs are bare arguments rather than one input
    object), ``fixed`` injects constant GraphQL arguments the agent never sees
    (e.g. ``confirm`` on a delete), and ``requires_user_actor`` rejects non-user
    MCP actors before execution for operations whose write attribution is a user FK.
    """

    operation: str
    name: str
    fields: tuple[ProjectionSpec, ...]
    description: str
    schema: str = "public"
    flatten: str | None = None
    id_arg: str | None = None
    limit_arg: str | None = None
    args: tuple[str, ...] = ()
    fixed: dict[str, Any] = field(default_factory=dict)
    requires_user_actor: bool = False


def register_graphql_tools(server: Any, specs: list[GraphQLTool]) -> None:
    """Compile each spec against its schema bucket and add it to the FastMCP server."""

    for spec in specs:
        server.add_tool(_compile(spec))


class _Projection(BaseModel):
    """One node in a tool's response projection tree (object nesting ≤ 2 levels).

    Resolved once at compile time so the runtime carries no closures and no live
    schema reference: ``wire`` is the schema field name, ``key`` the agent-facing
    snake_case name, ``is_id`` marks the public-id (``sqid``) translation, ``is_list``
    a list-valued branch, and ``children`` the nested projection for an object/list
    branch (empty for a scalar leaf). The node owns its own document fragment,
    output schema, and value extraction.
    """

    key: str
    wire: str
    is_id: bool = False
    is_list: bool = False
    children: tuple[_Projection, ...] = ()

    def selection(self) -> str:
        """Render this node's GraphQL selection: a leaf ``wire`` or ``wire { ... }``."""

        if not self.children:
            return self.wire
        inner = " ".join(child.selection() for child in self.children)
        return f"{self.wire} {{ {inner} }}"

    def json_schema(self, node: Any) -> dict[str, Any]:
        """Build this node's JSON output schema, descending ``node`` for child types."""

        if self.is_id:
            return {"type": "string"}
        if not self.children:
            schema, _ = _json_type(node.fields[self.wire].type)
            return schema
        child_node = _unwrap(node.fields[self.wire].type)
        obj = {"type": "object", "properties": {child.key: child.json_schema(child_node) for child in self.children}}
        return {"type": "array", "items": obj} if self.is_list else obj

    def value(self, row: dict[str, Any]) -> Any:
        """Extract this node's projected value from a parent GraphQL ``row``."""

        raw = row.get(self.wire)
        if not self.children:
            return raw
        if self.is_list:
            return [project_row(self.children, element) for element in (raw or [])]
        return None if raw is None else project_row(self.children, raw)


_Projection.model_rebuild()


def project_row(projections: tuple[_Projection, ...], row: dict[str, Any]) -> dict[str, Any]:
    """Project one GraphQL ``row`` into the agent shape (snake keys, id→sqid, nesting)."""

    return {projection.key: projection.value(row) for projection in projections}


class _CompiledTool(Tool):
    """A FastMCP tool that runs a GraphQL operation under the request actor.

    Carries the introspection-derived execution plan as data (no closures) so the
    subclass stays a plain pydantic model; :meth:`run` interprets it.
    """

    schema_name: str
    document: str
    payload_field: str
    node_type: str
    is_list: bool
    list_result_field: str | None = None
    leaves: tuple[_Projection, ...]
    flatten_arg: str | None = None
    flatten_fields: dict[str, str] = {}
    passthrough_args: dict[str, str] = {}
    id_arg: str | None = None
    id_arg_is_input: bool = False
    limit_arg: str | None = None
    limit_wire_arg: str | None = None
    fixed: dict[str, Any] = {}
    requires_user_actor: bool = False

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the operation and return the projected payload as structured content."""

        if self.requires_user_actor and not is_user_actor(current_actor()):
            raise ToolError("This operation requires a user actor.")
        data = await execute_under_actor(self.schema_name, self.document, self._variables(arguments))
        payload = data.get(self.payload_field)
        if self.is_list:
            rows = ((payload or {}).get(self.list_result_field) if self.list_result_field else payload) or []
            return ToolResult(structured_content={"result": [self._project(row) for row in rows]})
        if payload is None:
            raise ToolError("No matching record.")
        return ToolResult(structured_content=self._project(payload))

    def _variables(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Map agent args to GraphQL variables: limit→pagination, sqid→ID, passthrough, flatten."""

        args = dict(arguments)
        sqid = args.pop("sqid", None)
        variables: dict[str, Any] = {}
        if self.limit_arg and self.limit_arg in args:
            value = args.pop(self.limit_arg)
            if self.limit_wire_arg == "pagination":
                variables["pagination"] = {"limit": value}
            elif self.limit_wire_arg:
                variables[self.limit_wire_arg] = value
        if self.id_arg and sqid is not None:
            variables[self.id_arg] = {"id": str(sqid)} if self.id_arg_is_input else str(sqid)
        for agent_name, wire in self.passthrough_args.items():
            if agent_name in args:
                variables[wire] = args.pop(agent_name)
        if self.flatten_arg:
            obj: dict[str, Any] = {
                self.flatten_fields.get(key, to_camel_case(key)): value for key, value in args.items()
            }
            if sqid is not None and (not self.id_arg or "id" in self.flatten_fields.values()):
                obj["id"] = str(sqid)
            variables[self.flatten_arg] = obj
        variables.update(self.fixed)
        return variables

    def _project(self, row: dict[str, Any]) -> dict[str, Any]:
        """Project one GraphQL node into the agent shape (snake keys, id→sqid, nesting)."""

        return project_row(self.leaves, row)


def _compile(spec: GraphQLTool) -> _CompiledTool:
    """Introspect the schema bucket and build the runnable tool for ``spec``."""

    gc = GraphQLSchemas.from_discovery().graphql_schema(spec.schema)
    op_type, field = _root_field(gc, spec.operation)
    node, is_list, list_result_field = _return_node(field.type)
    _validate(spec, field, node)
    leaves = _plan(node, spec.fields)
    flatten_fields = _flatten_fields(field, spec)
    id_arg_is_input = _id_arg_is_input(field, spec)
    limit_wire_arg = _limit_wire_arg(field, spec)
    parameters = _input_schema(field, spec)
    document = _document(
        op_type,
        spec,
        field,
        leaves,
        is_list,
        list_result_field,
        limit_wire_arg,
    )
    return _CompiledTool(
        name=spec.name,
        description=spec.description,
        parameters=parameters,
        output_schema=_output_schema(node, leaves, is_list),
        annotations=ToolAnnotations(readOnlyHint=(op_type == "query")),
        schema_name=spec.schema,
        document=document,
        payload_field=spec.operation,
        node_type=node.name,
        is_list=is_list,
        list_result_field=list_result_field,
        leaves=leaves,
        flatten_arg=spec.flatten,
        flatten_fields=flatten_fields,
        passthrough_args={name: _arg_wire(field, name) for name in spec.args},
        id_arg=spec.id_arg,
        id_arg_is_input=id_arg_is_input,
        limit_arg=spec.limit_arg,
        limit_wire_arg=limit_wire_arg,
        fixed=spec.fixed,
        requires_user_actor=spec.requires_user_actor,
    )


def _root_field(gc: Any, operation: str) -> tuple[str, Any]:
    """Return ``("query"|"mutation", GraphQLField)`` for the named root operation."""

    if gc.query_type and operation in gc.query_type.fields:
        return "query", gc.query_type.fields[operation]
    if gc.mutation_type and operation in gc.mutation_type.fields:
        return "mutation", gc.mutation_type.fields[operation]
    raise ValueError(f"Operation {operation!r} is not a root field of the schema.")


def _return_node(rtype: Any) -> tuple[Any, bool, str | None]:
    """Unwrap the return type to its node type and list payload shape."""

    node = rtype.of_type if isinstance(rtype, GraphQLNonNull) else rtype
    if isinstance(node, GraphQLList):
        element = node.of_type
        while isinstance(element, GraphQLNonNull | GraphQLList):
            element = element.of_type
        return element, True, None
    fields = getattr(node, "fields", {})
    if "results" in fields:
        element = fields["results"].type
        while isinstance(element, GraphQLNonNull | GraphQLList):
            element = element.of_type
        return element, True, "results"
    return node, False, None


def _wire_field(node: Any, name: str) -> str:
    """Return the schema-owned wire field for one agent snake_case field."""

    return name if name in node.fields else to_camel_case(name)


def _arg_wire(field: Any, name: str) -> str:
    """Return the schema-owned wire argument for one agent snake_case arg name."""

    return name if name in field.args else to_camel_case(name)


def _plan(node: Any, fields: tuple[ProjectionSpec, ...]) -> tuple[_Projection, ...]:
    """Resolve a spec's ``fields`` into a projection tree against ``node``.

    Runs after :func:`_validate`, so every wire resolves and nesting is within range.
    """

    return tuple(_projection(node, entry) for entry in fields)


def _projection(node: Any, entry: ProjectionSpec) -> _Projection:
    """Resolve one ``fields`` entry against ``node`` into a :class:`_Projection`."""

    if isinstance(entry, str):
        if entry == "sqid":
            return _Projection(key="sqid", wire="id", is_id=True)
        return _Projection(key=entry, wire=_wire_field(node, entry))
    name, children = entry
    wire = _wire_field(node, name)
    gql_field = node.fields[wire]
    return _Projection(
        key=name,
        wire=wire,
        is_list=_is_list_type(gql_field.type),
        children=_plan(_unwrap(gql_field.type), children),
    )


def _is_list_type(gql_type: Any) -> bool:
    """Return whether a GraphQL field type is (or wraps) a list."""

    while isinstance(gql_type, GraphQLNonNull | GraphQLList):
        if isinstance(gql_type, GraphQLList):
            return True
        gql_type = gql_type.of_type
    return False


def _flatten_fields(field: Any, spec: GraphQLTool) -> dict[str, str]:
    """Return agent-name → input-wire-name mapping for a flattened input."""

    if spec.flatten is None:
        return {}
    input_fields = _unwrap(field.args[spec.flatten].type).fields
    return {to_snake_case(name): name for name in input_fields}


def _id_arg_is_input(field: Any, spec: GraphQLTool) -> bool:
    """Return whether the configured id arg is an input object."""

    if spec.id_arg is None:
        return False
    return isinstance(_unwrap(field.args[spec.id_arg].type), GraphQLInputObjectType)


def _limit_wire_arg(field: Any, spec: GraphQLTool) -> str | None:
    """Return the root argument that carries an agent ``limit`` value."""

    if spec.limit_arg is None:
        return None
    return spec.limit_arg if spec.limit_arg in field.args else "pagination"


def _validate(spec: GraphQLTool, field: Any, node: Any) -> None:
    """Fail fast with a clear message when a spec names a field or arg the schema lacks.

    ``_plan``/``_output_schema``/``_document`` index ``node.fields[wire]`` and
    ``field.args[arg]`` directly, so an unknown name would otherwise surface as a bare
    ``KeyError`` deep in introspection. Validate here so a bad :class:`GraphQLTool` breaks
    ``angee dev`` startup naming the operation and the offending name (matching
    ``_root_field``'s fail-fast).
    """

    _validate_fields(spec, node, spec.fields, depth=1)
    driven = [arg for arg in (_limit_wire_arg(field, spec), spec.id_arg, spec.flatten) if arg]
    driven += [_arg_wire(field, name) for name in spec.args]
    driven += list(spec.fixed)
    unknown_args = [arg for arg in driven if arg not in field.args]
    if unknown_args:
        raise ImproperlyConfigured(f"MCP tool {spec.name!r}: {spec.operation} takes no argument(s) {unknown_args}.")


def _validate_fields(spec: GraphQLTool, node: Any, fields: tuple[ProjectionSpec, ...], *, depth: int) -> None:
    """Recurse the projection tree, rejecting unknown fields and nesting beyond depth 2.

    A branch entry projects a nested object/list; its children are checked against the
    branch's own node. ``sqid`` is always valid (it maps to the GraphQL ``id`` field).
    """

    unknown: list[str] = []
    for entry in fields:
        if isinstance(entry, str):
            if entry != "sqid" and _wire_field(node, entry) not in node.fields:
                unknown.append(_wire_field(node, entry))
            continue
        name, children = entry
        wire = _wire_field(node, name)
        if wire not in node.fields:
            unknown.append(wire)
            continue
        if depth > 2:
            raise ImproperlyConfigured(
                f"MCP tool {spec.name!r}: projection {name!r} nests deeper than the 2-level "
                f"limit (projected by {spec.operation})."
            )
        _validate_fields(spec, _unwrap(node.fields[wire].type), children, depth=depth + 1)
    if unknown:
        raise ImproperlyConfigured(
            f"MCP tool {spec.name!r}: {node.name} has no field(s) {unknown} (projected by {spec.operation})."
        )


def _input_schema(field: Any, spec: GraphQLTool) -> dict[str, Any]:
    """Build the tool's JSON input schema from the one arg the spec drives."""

    properties: dict[str, Any] = {}
    required: list[str] = []
    if spec.limit_arg:
        properties[spec.limit_arg] = {"type": "integer", "description": "Maximum rows to return."}
    if spec.id_arg:
        properties["sqid"] = {"type": "string", "description": "Public id of the target record."}
        required.append("sqid")
    if spec.flatten:
        for name, input_field in _unwrap(field.args[spec.flatten].type).fields.items():
            if name == "id":
                properties["sqid"] = {"type": "string", "description": "Public id of the target record."}
                required.append("sqid")
                continue
            schema, non_null = _json_type(input_field.type)
            properties[to_snake_case(name)] = schema
            if non_null and input_field.default_value is Undefined:
                required.append(to_snake_case(name))
    for name in spec.args:
        arg = field.args[_arg_wire(field, name)]
        schema, non_null = _json_type(arg.type)
        properties[name] = schema if not arg.description else {**schema, "description": arg.description}
        if non_null and arg.default_value is Undefined:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _document(
    op_type: str,
    spec: GraphQLTool,
    field: Any,
    leaves: tuple[_Projection, ...],
    is_list: bool,
    list_result_field: str | None,
    limit_wire_arg: str | None,
) -> str:
    """Render the GraphQL operation document with variable defs and the selection set."""

    used = [arg for arg in (limit_wire_arg, spec.id_arg, spec.flatten) if arg]
    used += [_arg_wire(field, name) for name in spec.args]
    used += list(spec.fixed)
    used = list(dict.fromkeys(used))
    var_defs = ", ".join(f"${arg}: {field.args[arg].type}" for arg in used)
    call_args = ", ".join(f"{arg}: ${arg}" for arg in used)
    selection = " ".join(projection.selection() for projection in leaves)
    body = f"{list_result_field} {{ {selection} }}" if list_result_field else selection
    call = f"{spec.operation}({call_args})" if call_args else spec.operation
    header = f"({var_defs})" if var_defs else ""
    return f"{op_type} {header} {{ {call} {{ {body} }} }}"


def _output_schema(node: Any, leaves: tuple[_Projection, ...], is_list: bool) -> dict[str, Any]:
    """Build the advertised output schema (an object; a list wraps under ``result``)."""

    properties = {projection.key: projection.json_schema(node) for projection in leaves}
    row_schema = {"type": "object", "properties": properties}
    if is_list:
        return {"type": "object", "properties": {"result": {"type": "array", "items": row_schema}}}
    return row_schema


def _json_type(gql_type: Any) -> tuple[dict[str, Any], bool]:
    """Map a GraphQL input/output type to ``(JSON Schema, is_non_null)``."""

    if isinstance(gql_type, GraphQLNonNull):
        schema, _ = _json_type(gql_type.of_type)
        return schema, True
    if isinstance(gql_type, GraphQLList):
        item, _ = _json_type(gql_type.of_type)
        return {"type": "array", "items": item}, False
    if isinstance(gql_type, GraphQLEnumType):
        return {"type": "string", "enum": list(gql_type.values)}, False
    if isinstance(gql_type, GraphQLScalarType):
        name = _SCALAR_JSON.get(gql_type.name)
        return ({"type": name} if name else {}), False
    if isinstance(gql_type, GraphQLInputObjectType):
        return {"type": "object"}, False
    return {}, False


def _unwrap(gql_type: Any) -> Any:
    """Strip NonNull/List wrappers to the named type."""

    while isinstance(gql_type, GraphQLNonNull | GraphQLList):
        gql_type = gql_type.of_type
    return gql_type
