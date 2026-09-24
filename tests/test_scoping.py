"""Shared elevation preserves the outer authorization and audit context."""

import pytest
from rebac import SubjectRef, actor_context, current_actor, system_context
from rebac.actors import current_sudo_reason, is_sudo

from angee.base.scoping import elevated


@pytest.mark.django_db
def test_elevated_preserves_outer_system_reason() -> None:
    with system_context(reason="test.outer"):
        with elevated(reason="test.inner"):
            assert is_sudo()
            assert current_sudo_reason() == "test.outer"
        assert current_sudo_reason() == "test.outer"


@pytest.mark.django_db
def test_elevated_restores_actor_scope_after_failure() -> None:
    actor = SubjectRef.of("auth/user", "test-scoping")
    with actor_context(actor):
        assert not is_sudo()
        with pytest.raises(ValueError, match="failed"):
            with elevated(reason="test.elevated"):
                assert is_sudo()
                assert current_sudo_reason() == "test.elevated"
                raise ValueError("failed")
        assert not is_sudo()
        assert current_actor() == actor
