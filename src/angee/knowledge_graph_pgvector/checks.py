"""Validate graph deployment options through Django's native check registry."""

from typing import Any

from django.core.checks import Error, register
from pydantic import ValidationError

from angee.knowledge_graph_pgvector.options import GraphOptions


@register()
def check_graph_options(app_configs: Any, **kwargs: Any) -> list[Error]:
    try:
        GraphOptions.from_settings()
    except ValidationError as error:
        return [Error(str(error), id="knowledge_graph_pgvector.E001")]
    return []
