"""Tests for the IMAP-owned durable sync work ledger."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.messaging_integrate_imap.models import ImapAttachmentWork as AbstractImapAttachmentWork
from angee.messaging_integrate_imap.models import ImapMessageWork as AbstractImapMessageWork
from angee.messaging_integrate_imap.models import ImapSyncRun as AbstractImapSyncRun
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.test_messaging import MESSAGING_TEST_MODELS
from tests.test_messaging_graphql import Channel


class ImapSyncRun(AbstractImapSyncRun):
    """Concrete IMAP sync run used by ledger tests."""

    class Meta(AbstractImapSyncRun.Meta):
        """Django model options for the canonical test IMAP sync run."""

        abstract = False
        app_label = "messaging_integrate_imap"
        db_table = "test_messaging_imap_sync_run"


class ImapMessageWork(AbstractImapMessageWork):
    """Concrete IMAP message work row used by ledger tests."""

    class Meta(AbstractImapMessageWork.Meta):
        """Django model options for the canonical test IMAP message work row."""

        abstract = False
        app_label = "messaging_integrate_imap"
        db_table = "test_messaging_imap_message_work"


class ImapAttachmentWork(AbstractImapAttachmentWork):
    """Concrete IMAP attachment work row used by ledger tests."""

    class Meta(AbstractImapAttachmentWork.Meta):
        """Django model options for the canonical test IMAP attachment work row."""

        abstract = False
        app_label = "messaging_integrate_imap"
        db_table = "test_messaging_imap_attachment_work"


IMAP_WORK_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel, ImapSyncRun, ImapMessageWork, ImapAttachmentWork)


@pytest.fixture
def imap_work_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete messaging and IMAP ledger tables."""

    del transactional_db
    created_models = _create_missing_tables(IMAP_WORK_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(IMAP_WORK_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _imap_channel(**config: Any) -> Any:
    """Create an IMAP Channel row with a basic-auth credential."""

    return make_integration(
        "imap",
        kind=CredentialKind.BASIC_AUTH,
        material={"username": "ada@example.com", "password": "pw"},
        model=Channel,
        backend_class="imap",
        config={"host": "192.0.2.10", **config},
    )


@pytest.mark.django_db(transaction=True)
def test_imap_message_work_is_unique_per_channel_mailbox_uid(imap_work_tables: None) -> None:
    """Discovery converges on one durable work row for one IMAP UID identity."""

    del imap_work_tables
    with system_context(reason="test.imap.work.unique"):
        channel = _imap_channel()
        run = ImapSyncRun.objects.start_for_channel(channel)
        first = ImapMessageWork.objects.upsert_discovered(
            run,
            mailbox="INBOX",
            uidvalidity=100,
            uid=10,
            size=1024,
            flags=(b"\\Seen",),
            internal_date=datetime(2026, 7, 2, 9, 30, tzinfo=UTC),
        )
        second = ImapMessageWork.objects.upsert_discovered(
            run,
            mailbox="INBOX",
            uidvalidity=100,
            uid=10,
            size=2048,
            flags=(b"\\Seen", b"\\Answered"),
            internal_date=datetime(2026, 7, 2, 9, 31, tzinfo=UTC),
        )

    assert first.pk == second.pk
    second.refresh_from_db()
    assert second.run_id == run.pk
    assert second.channel_id == channel.pk
    assert second.size_bytes == 2048
    assert second.flags == ["\\Seen", "\\Answered"]
    assert second.status == ImapMessageWork.WorkStatus.PENDING


@pytest.mark.django_db(transaction=True)
def test_imap_attachment_work_claim_ready_returns_pending_rows(imap_work_tables: None) -> None:
    """Attachment workers claim pending rows through the ledger manager."""

    del imap_work_tables
    with system_context(reason="test.imap.work.claim"):
        channel = _imap_channel()
        run = ImapSyncRun.objects.start_for_channel(channel)
        message_work = ImapMessageWork.objects.upsert_discovered(
            run,
            mailbox="INBOX",
            uidvalidity=100,
            uid=10,
            size=1024,
            flags=(),
            internal_date=None,
        )
        first = ImapAttachmentWork.objects.create(message_work=message_work, section="2")
        ImapAttachmentWork.objects.create(
            message_work=message_work,
            section="3",
            status=ImapAttachmentWork.WorkStatus.DONE,
        )

    assert list(ImapAttachmentWork.objects.claim_ready(limit=5)) == [first]
