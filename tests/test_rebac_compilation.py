"""Framework contracts for compiled permission recursion."""

from __future__ import annotations

from rebac.compile.program import program_errors
from rebac.schema import parse_zed


def test_recursive_parent_exclusion_is_rejected() -> None:
    """E016 rejects a negative dependency inside the task's recursive read path."""

    schema = parse_zed("""
        definition auth/user {}
        definition projects/task {
            relation parent: projects/task
            relation viewer: auth/user
            permission read = viewer - parent->read
        }
    """)
    assert [issue.id for issue in program_errors(schema)] == ["rebac.E016"]
