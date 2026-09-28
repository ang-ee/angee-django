"""Small fixtures shared by the IAM contract campaign."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from angee.iam.events import person_created
from tests.conftest import create_platform_admin
from tests.test_messaging import Person as Person

User = get_user_model()


@pytest.fixture
def iam_admin(composed_tables: None) -> Any:
    """Use the existing permission sync and a real, non-superuser role grant."""

    return create_platform_admin("campaign-admin", password=None)


@pytest.fixture
def person_events() -> Iterator[list[dict[str, Any]]]:
    """Observe the real signal without replacing its parties receiver."""

    events = []

    def receive(sender, instance, **kwargs):
        events.append({"sender": sender, "user": instance, "atomic": connection.in_atomic_block})

    person_created.connect(receive, weak=False)
    try:
        yield events
    finally:
        person_created.disconnect(receive)


@pytest.fixture
def legacy_person_emails(composed_tables: None) -> Iterator[None]:
    """Reproduce a pre-constraint table, restoring uniqueness even on failure."""

    constraint = next(c for c in User._meta.constraints if c.name == "iam_user_person_email_unique")
    with connection.schema_editor() as editor:
        editor.remove_constraint(User, constraint)
    try:
        yield
    finally:
        for pk in User._base_manager.values_list("pk", flat=True):
            User._base_manager.filter(pk=pk).update(email=f"restored-{pk}@example.com")
        with connection.schema_editor() as editor:
            editor.add_constraint(User, constraint)
