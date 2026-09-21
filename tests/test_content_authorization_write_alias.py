"""Content write entrypoints reject unsupported modeled-resource authorization aliases."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from angee.dashboards.models import Dashboard, DashboardManager
from angee.intake.models import NeedManager
from angee.spaces.managers import MembershipManager
from tests.conftest import MarkdownPage, Page, RecordBinding, Vault


@pytest.mark.parametrize(
    "attempt",
    [
        pytest.param(
            lambda: Vault.objects.create_for(None, using="unconfigured_writer"),
            id="vault-create",
        ),
        pytest.param(
            lambda: Page.objects.create_in(None, using="unconfigured_writer"),
            id="page-create",
        ),
        pytest.param(
            lambda: RecordBinding.objects.upsert(
                page=Page(pk=1), target=Page(pk=2), using="unconfigured_writer"
            ),
            id="binding-upsert",
        ),
        pytest.param(
            lambda: RecordBinding.objects.unbind(
                page=Page(pk=1), target=Page(pk=2), using="unconfigured_writer"
            ),
            id="binding-unbind",
        ),
        pytest.param(
            lambda: MarkdownPage.objects.write_body(None, "body", using="unconfigured_writer"),
            id="markdown-write",
        ),
        pytest.param(
            lambda: DashboardManager().create_personal(
                None, name="Dashboard", client_creation_key="create", using="unconfigured_writer"
            ),
            id="dashboard-create",
        ),
        pytest.param(
            lambda: DashboardManager().save_snapshot(
                None, scope="personal", scope_key=None, persisted_id=1,
                expected_revision=1, snapshot={}, using="unconfigured_writer",
            ),
            id="dashboard-save",
        ),
        pytest.param(
            lambda: DashboardManager().reset_snapshot(None, expected_revision=1, using="unconfigured_writer"),
            id="dashboard-reset",
        ),
        pytest.param(
            lambda: Dashboard.set_personal_archived(
                SimpleNamespace(), archived=True, expected_revision=1, using="unconfigured_writer"
            ),
            id="dashboard-archive",
        ),
        pytest.param(
            lambda: MembershipManager().add_confirmed(
                group=None, party=None, role="member", using="unconfigured_writer"
            ),
            id="membership-create",
        ),
        pytest.param(
            lambda: NeedManager().capture(target=None, body="Request", using="unconfigured_writer"),
            id="need-capture",
        ),
    ],
)
def test_content_authorization_rejects_before_query_or_transaction(attempt: Callable[[], object]) -> None:
    """No configured alias, persisted input, actor, or database access is needed to reject."""

    with pytest.raises(ValidationError, match="default authorization database is required"):
        attempt()
