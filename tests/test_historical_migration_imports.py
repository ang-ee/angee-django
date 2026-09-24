"""Materialized migration imports remain usable after framework refactors."""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

FIXTURE = Path(__file__).with_name("fixtures") / "historical_migration_imports.txt"
HEADERS = tuple(
    header for line in FIXTURE.read_text(encoding="utf-8").splitlines() if (header := line.partition("#")[0].strip())
)
STATEMENTS = tuple(ast.parse(header).body[0] for header in HEADERS)
MODULES: set[str] = set()
for statement in STATEMENTS:
    if isinstance(statement, ast.ImportFrom):
        assert statement.module is not None
        imported_modules = [statement.module]
    elif isinstance(statement, ast.Import):
        imported_modules = [alias.name for alias in statement.names]
    else:
        continue
    for module in imported_modules:
        parts = module.split(".")
        MODULES.update(".".join(parts[:index]) for index in range(1, len(parts) + 1))


@pytest.mark.parametrize("statement", STATEMENTS, ids=HEADERS)
def test_historical_migration_import_resolves(statement: ast.stmt) -> None:
    """Resolve names through Python, including module-level compatibility aliases."""

    if isinstance(statement, ast.Import):
        assert len(statement.names) == 1
        module_name = statement.names[0].name
        attributes = []
    elif isinstance(statement, ast.ImportFrom):
        assert statement.module is not None and len(statement.names) == 1
        module_name = statement.module
        attributes = [statement.names[0].name]
    else:
        assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Attribute)
        reference = ast.unparse(statement.value)
        module_name = max(
            (module for module in MODULES if reference == module or reference.startswith(f"{module}.")),
            key=len,
        )
        suffix = reference.removeprefix(module_name).removeprefix(".")
        attributes = suffix.split(".") if suffix else []

    if module_name == "angee.messaging_integrate_telegram" and importlib.util.find_spec(module_name) is None:
        pytest.skip("angee-messaging-bridges is optional and its Telegram addon is absent from these test settings")

    resolved = importlib.import_module(module_name)
    for attribute in attributes:
        resolved = getattr(resolved, attribute)
