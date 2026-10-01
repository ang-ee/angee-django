"""Consumer query shape for the largest composed permission plans."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context
from rebac.resources import model_for_resource_type

from angee.decisions.testing.models import Decision, DecisionGroup
from angee.workflows.states import WorkflowStatus
from angee.workflows.testing.models import Workflow
from tests import test_messaging as _messaging_models  # noqa: F401 -- register source model
from tests import test_productivity_deferred_save as _task_relation_models  # noqa: F401 -- register source model
from tests.conftest import Backend, Drive, Folder, Page, Vault, create_user, make_integration
from tests.extraction_models import Extraction as _Extraction  # noqa: F401 -- register source model
from tests.messaging_models import Channel, Thread
from tests.money_models import Currency, CurrencyRate
from tests.spaces_models import Group


@pytest.mark.parametrize(
    ("resource_type", "permission"),
    (
        ("messaging/message_edge", "read"),
        ("projects/task_relation", "read"),
        ("knowledge/record_binding", "read"),
        ("projects/task", "comment"),
        ("decisions/decision", "act"),
        ("workflows_extraction/extraction", "read"),
        ("storage/file", "read"),
        ("storage/file_attachment", "read"),
        ("projects/link", "read"),
        ("messaging/thread", "read"),
    ),
)
def test_large_permission_reads_issue_one_statement(
    composed_tables: None, resource_type: str, permission: str,
) -> None:
    """An index-backed actor scope remains one statement for each large plan."""

    del composed_tables
    model = model_for_resource_type(resource_type)
    assert model is not None, resource_type
    actor = create_user(f"index-{resource_type.replace('/', '-')}")
    queryset = model.objects.with_actor(actor).with_action(permission).scoped()
    with CaptureQueriesContext(connection) as queries:
        list(queryset.values_list("pk", flat=True)[:1])
    assert len(queries) == 1, (resource_type, permission, queries)


@pytest.mark.parametrize("resource_type", ("storage/folder", "knowledge/page", "workflows/workflow"))
def test_recursive_read_sql_is_constant_across_fifty_levels(composed_tables: None, resource_type: str) -> None:
    """A deeper hierarchy changes indexed data, never the actor-scope SQL shape."""

    del composed_tables
    actor = create_user(f"depth-{resource_type.replace('/', '-')}")
    model = model_for_resource_type(resource_type)
    assert model is not None

    def scope_sql() -> str:
        return str(model.objects.with_actor(actor).with_action("read").scoped().query)

    shallow = scope_sql()
    with system_context(reason="tests.rebac.index.depth"):
        if resource_type == "storage/folder":
            storage = Backend.objects.create(slug="depth", label="Depth", backend_class="local")
            drive = Drive.objects.create(slug="depth", name="Depth", prefix="depth", backend=storage, owner=actor)
            parent = None
            for level in range(50):
                parent = Folder.objects.create(drive=drive, parent=parent, name=f"level-{level}")
        elif resource_type == "knowledge/page":
            vault = Vault.objects.create(name="Depth", owner=actor)
            parent = None
            for level in range(50):
                parent = Page.objects.create(vault=vault, parent=parent, title=f"level-{level}")
        else:
            # Publishing normally points each version at its head. A synthetic
            # chain stresses the recursive index read without changing policy.
            start = Workflow._base_manager.order_by("-pk").values_list("pk", flat=True).first() or 0
            Workflow._base_manager.bulk_create(
                Workflow(
                    pk=start + level,
                    name=f"level-{level}",
                    status=WorkflowStatus.PUBLISHED,
                    version=level,
                    published_from_id=start + level - 1 if level > 1 else None,
                    created_by=actor,
                )
                for level in range(1, 51)
            )
    call_command("rebac", "index", "rebuild", verbosity=0)
    assert scope_sql() == shallow


def test_owned_relation_writes_leave_the_permission_index_verified(composed_tables: None) -> None:
    """M2M, reverse FK, SET_NULL, rate filters and ContentType deletion stay indexed."""

    del composed_tables
    actor = create_user("index-owned-writes")
    with system_context(reason="tests.rebac.index.owned_writes"):
        group = Group.objects.create(name="Index", slug="index")
        thread = Thread.objects.create(modality=Thread.Modality.GROUP)
        thread.groups.add(group)
        thread.groups.remove(group)
        thread.groups.add(group)
        thread.groups.clear()

        decision_group = DecisionGroup.objects.create(issuer=actor)
        decision = Decision.objects.create(
            group=decision_group, index=0, kind="index", form_schema={}, max_attempts=1,
        )
        decision.assignees.add(actor)
        decision.assignees.clear()

        channel = make_integration("index-channel", model=Channel, backend_class="manual")
        group.channels.add(channel, bulk=True)
        vault = Vault.objects.create(name="Index team", owner=actor, team=group)
        group.delete()
        assert Channel._base_manager.get(pk=channel.pk).team_id is None
        assert Vault._base_manager.get(pk=vault.pk).team_id is None

        currency = Currency.objects.create(code="EUR", name="Euro")
        rate = CurrencyRate.objects.create(currency=currency, date=date(2030, 1, 1), rate=Decimal("1"))
        content_type = ContentType.objects.get_for_model(Currency)
        rate.context_content_type = content_type
        rate.context_object_id = str(currency.pk)
        rate.reference_currency = currency
        rate.save(update_fields=("context_content_type", "context_object_id", "reference_currency"))
        rate.context_content_type = None
        rate.context_object_id = ""
        rate.reference_currency = None
        rate.save(update_fields=("context_content_type", "context_object_id", "reference_currency"))
        content_type.delete()

    call_command("rebac", "index", "verify", verbosity=0)
