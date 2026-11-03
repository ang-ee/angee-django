import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.http import HttpResponse
from django.test import RequestFactory

from angee.provenance.engine.middleware import ChangesetMiddleware, ChangesetScopeMiddleware
from tests.provenance_engine.capture_app.models import Note
from tests.provenance_engine.storage.models import Changeset

pytestmark = pytest.mark.django_db(transaction=True)


def test_http_status_marks_handled_failure():
    def response(request):
        Note.objects.create(title="request")
        return HttpResponse(status=400)

    ChangesetMiddleware(response)(RequestFactory().get("/"))
    assert Changeset.objects.get().outcome == "failure"


def test_async_http_and_websocket():
    async def response(request):
        await sync_to_async(Note.objects.create)(title="request")
        return HttpResponse()

    async def request():
        try:
            await ChangesetMiddleware(response)(RequestFactory().get("/"))
        finally:
            await sync_to_async(connections.close_all)()

    asyncio.run(request())

    async def inner(scope, receive, send):
        await sync_to_async(Note.objects.create)(title="socket")

    async def websocket():
        try:
            await ChangesetScopeMiddleware(inner)({"type": "websocket"}, None, None)
        finally:
            await sync_to_async(connections.close_all)()

    asyncio.run(websocket())
    assert Changeset.objects.count() == 2
