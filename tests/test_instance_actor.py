"""Instance actor resolution preserves native pinning through elevation."""

from contextlib import nullcontext

import pytest
from rebac import SubjectRef, actor_context, system_context, to_subject_ref

from angee.base.actors import instance_actor
from tests.conftest import create_user
from tests.core_persistence import OwnedRow, OwnershipContainer, ownership_tables  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("pinned", [None, SubjectRef.of("auth/user", "17"), SubjectRef.of("service/account", "worker")])
@pytest.mark.parametrize("ambient", [None, SubjectRef.of("auth/user", "29")])
@pytest.mark.parametrize("elevation", ["none", "instance", "ambient"])
def test_pinned_actor_wins_over_ambient_actor_even_during_elevated_writes(pinned, ambient, elevation):
    row = OwnedRow()
    if pinned is not None:
        row.with_actor(pinned)
    if elevation == "instance":
        row.sudo(reason="test.instance_actor")
    with actor_context(ambient) if ambient is not None else nullcontext():
        with system_context(reason="test.instance_actor") if elevation == "ambient" else nullcontext():
            assert instance_actor(row) == (pinned if pinned is not None else ambient)


@pytest.mark.parametrize("ambient", [
    None, SubjectRef.of("auth/user", "29"), SubjectRef.of("service/account", "worker"),
])
def test_plain_django_instance_uses_only_ambient_actor(ambient):
    instance = OwnershipContainer()
    assert not hasattr(instance, "actor")
    with actor_context(ambient) if ambient is not None else nullcontext():
        assert instance_actor(instance) == ambient
        with system_context(reason="test.instance_actor.plain"):
            assert instance_actor(instance) == ambient


@pytest.mark.usefixtures("ownership_tables")
def test_pinned_actor_drives_both_audit_and_owner_defaults_under_elevation():
    pinned, ambient = create_user("pinned"), create_user("ambient")
    row = OwnedRow().with_actor(pinned).sudo(reason="test.instance_actor.save")
    with actor_context(ambient):
        row.save()
    assert instance_actor(row) == to_subject_ref(pinned)
    assert row.owner_id == row.created_by_id == pinned.pk
