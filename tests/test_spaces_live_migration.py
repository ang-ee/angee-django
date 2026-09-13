"""Source migration contracts for the spaces live-backing cutover."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from angee.base.fields import SqidField
from angee.spaces.runtime_migrations.live_relation_backing import remove_evidenced_mirrors


class _Rows:
    def __init__(self, values: list[tuple[Any, ...]] | None = None) -> None:
        self.values = values or []
        self.deleted: list[dict[str, Any]] = []
        self.pending: dict[str, Any] = {}

    def using(self, database: str) -> _Rows:
        assert database == "default"
        return self

    def filter(self, **kwargs: Any) -> _Rows:
        if self.values:
            return self
        self.pending = kwargs
        return self

    def values_list(self, *fields: str) -> _Rows:
        assert fields
        return self

    def iterator(self) -> Any:
        return iter(self.values)

    def delete(self) -> None:
        self.deleted.append(self.pending)


def test_spaces_cleanup_derives_exact_roster_and_thread_mirror_pairs() -> None:
    """Populated source evidence deletes exact rows in both physical stores."""

    denormalized = _Rows()
    registry = _Rows()
    membership_rows = _Rows([(11, "moderator", 12)])
    audience_rows = _Rows([(13, 11)])
    group = object()
    thread = SimpleNamespace()
    thread_field = SimpleNamespace(
        name="thread",
        remote_field=SimpleNamespace(model=thread),
    )
    group_field = SimpleNamespace(
        name="group",
        remote_field=SimpleNamespace(model=group),
    )
    through = SimpleNamespace(
        _meta=SimpleNamespace(fields=(thread_field, group_field)),
        _base_manager=audience_rows,
    )
    thread.groups = SimpleNamespace(through=through)
    models = {
        ("rebac", "Relationship"): SimpleNamespace(_base_manager=denormalized),
        ("rebac", "RelationshipRegistry"): SimpleNamespace(_base_manager=registry),
        ("spaces", "Membership"): SimpleNamespace(_base_manager=membership_rows),
        ("spaces", "Group"): group,
        ("messaging", "Thread"): thread,
    }
    historical_apps = SimpleNamespace(get_model=lambda app, model: models[(app, model)])
    editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))

    remove_evidenced_mirrors(historical_apps, editor)

    group_id = _public_id(11, "grp_")
    user_id = _public_id(12, "usr_")
    thread_id = _public_id(13, "thr_")
    assert denormalized.deleted == [
        {
            "resource_type": "spaces/group",
            "resource_id": group_id,
            "relation": "moderator",
            "subject_type": "auth/user",
            "subject_id": user_id,
            "optional_subject_relation": "",
            "caveat_name": "",
        },
        {
            "resource_type": "messaging/thread",
            "resource_id": thread_id,
            "relation": "group",
            "subject_type": "spaces/group",
            "subject_id": group_id,
            "optional_subject_relation": "",
            "caveat_name": "",
        },
    ]
    assert len(registry.deleted) == 2
    assert all("resource_fk__resource_type" in row for row in registry.deleted)
    assert all("subject_fk__resource_type" in row for row in registry.deleted)


def _public_id(value: int, prefix: str) -> str:
    return SqidField(real_field_name="id", prefix=prefix, min_length=8).public_id_from_value(value)
