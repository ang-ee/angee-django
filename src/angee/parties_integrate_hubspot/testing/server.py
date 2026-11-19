"""A scripted HubSpot account for tests of HubSpot sync.

The account states HubSpot's facts itself (its host, limits, scope names and
the property a contact's modified time is stated by), so a wrong constant in the
client fails a test instead of agreeing with it. Only the API version, the
client's own choice, comes from the client.
"""

from __future__ import annotations

import json
import operator
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from angee.integrate.testing.http import mock_transport
from angee.parties_integrate_hubspot.client import API_VERSION

TOKEN = "pat-na1-00000000-0000-4000-8000-000000000000"
"""The invented service key the scripted account accepts."""
HUB_ID = 1234567
"""The scripted account's hub (portal) id."""
CREATED = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
"""The creation time a record states unless ``add`` names one."""
VENDOR_TEXT = "Vendor text that must not reach an operator."
"""The ``message`` every scripted error states, which a client must never repeat."""

_HOST = "https://api.hubapi.com"
_MODIFIED_PROPERTY = {"contacts": "lastmodifieddate"}
_STANDARD_PROPERTIES = {
    "contacts": frozenset(
        {
            "hs_object_id",
            "createdate",
            "lastmodifieddate",
            "hs_merged_object_ids",
            "firstname",
            "lastname",
            "email",
            "hs_additional_emails",
            "phone",
            "mobilephone",
            "jobtitle",
            "company",
            "address",
            "city",
            "state",
            "zip",
            "country",
        }
    )
}
_READ_SCOPE = {"contacts": "crm.objects.contacts.read"}
_RECORD_ID = "hs_object_id"
_SEARCH_OPERATORS = {"EQ": operator.eq, "GT": operator.gt}
_LIST_PAGE_MAX = 100
_SEARCH_PAGE_MAX = 200
_BATCH_READ_MAX = 100
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def error_response(
    status: int,
    *,
    category: str | None = None,
    policy_name: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> httpx2.Response:
    """Return one HubSpot error answer; its ``message`` is :data:`VENDOR_TEXT`."""

    body: dict[str, Any] = {
        "status": "error",
        "message": VENDOR_TEXT,
        "correlationId": "00000000-0000-4000-8000-000000000001",
    }
    if category:
        body["category"] = category
    if policy_name:
        body |= {"errorType": "RATE_LIMIT", "policyName": policy_name}
    return httpx2.Response(status, json=body, headers=headers)


class HubSpotServer:
    """One HubSpot account in memory, which ``serve`` makes every outbound request reach.

    ``records`` maps each object type (``contacts``) to its records by numeric id;
    ``add``, ``update``, ``archive``, ``delete`` and ``merge`` change them as HubSpot
    would, stamping the type's modified property. ``properties`` names the
    properties the account defines for each type: HubSpot's standard ones, and
    every property a record was given. A read answers each requested property the
    account defines, ``None`` when the record states none, and leaves out a name
    it does not define, as HubSpot ignores one. The account answers token
    introspection, account details, list pages (by id, ``archived`` lists the
    soft-deleted), searches and batch reads (207 with an error per category). A
    search takes ``EQ`` and ``GT`` filters on the modified time (epoch
    milliseconds) or the record id, requires every filter of its one group, and
    sorts ascending on one of them; HubSpot states no order for records with one
    sort value, so the account puts the higher id first. It answers a query's
    first page only and refuses an offset or any other shape. It refuses a wrong
    bearer with 401 and a missing read scope with 403 ``MISSING_SCOPES``.

    ``clock`` is HubSpot's time, which every answer states in its ``Date`` header
    (to the second, as HTTP dates are), queued answers included. It starts at the
    epoch, moves forward with every change the account stamps, as HubSpot stamps a
    change with its own time, and a test sets it on to let time pass. A change
    made with a ``lag`` stays out of search results until the clock has passed it
    by that much, as HubSpot's search index catches up with its writes; lists and
    batch reads show it at once.

    Server-side behaviour is scripted rather than patched: ``queue`` answers the
    next requests before any routing (an exception models an unreachable host,
    and ``None`` lets one request through to the account),
    ``read_errors`` names the ids a batch read fails on and the error category it
    states for each, and ``resolves_merged`` makes a batch read of a merged id
    return the record it was merged into, under that record's id. Every request is
    recorded in ``requests``.
    """

    def __init__(self, *, scopes: Iterable[str] = (_READ_SCOPE["contacts"],)) -> None:
        """Serve an empty account; nothing is recorded until a client sends a request."""

        self.token = TOKEN
        self.hub_id = HUB_ID
        self.scopes = set(scopes)
        self.records: dict[str, dict[str, dict[str, Any]]] = {object_type: {} for object_type in _MODIFIED_PROPERTY}
        self.properties = {object_type: set(names) for object_type, names in _STANDARD_PROPERTIES.items()}
        self.merged: dict[str, dict[str, str]] = {object_type: {} for object_type in _MODIFIED_PROPERTY}
        self.resolves_merged = True
        self.clock = _EPOCH
        self.read_errors: dict[str, str] = {}
        self.queued: list[httpx2.Response | BaseException | None] = []
        self.requests: list[httpx2.Request] = []
        self._next_id = 101

    def add(
        self,
        object_type: str,
        properties: Mapping[str, str | None] | None = None,
        *,
        modified: datetime,
        created: datetime = CREATED,
        record_id: str = "",
        lag: timedelta = timedelta(0),
    ) -> str:
        """Create one record stating ``properties`` and return its id (the next free one unless named)."""

        record_id = record_id or str(self._next_id)
        self._next_id = max(self._next_id, int(record_id) + 1)
        self.properties[object_type].update(properties or {})
        self.records[object_type][record_id] = {
            "properties": {**(properties or {}), "hs_object_id": record_id, "createdate": _iso(created)},
            "created": created,
            "archived_at": None,
        }
        self._stamp(object_type, record_id, modified, lag)
        return record_id

    def update(
        self,
        object_type: str,
        record_id: str,
        properties: Mapping[str, str | None],
        *,
        modified: datetime,
        lag: timedelta = timedelta(0),
    ) -> None:
        """Change ``properties`` of one record and stamp ``modified`` on it."""

        self.properties[object_type].update(properties)
        self.records[object_type][record_id]["properties"].update(properties)
        self._stamp(object_type, record_id, modified, lag)

    def archive(self, object_type: str, record_id: str, *, at: datetime) -> None:
        """Soft-delete one record: it leaves lists, searches and batch reads, and lists as archived."""

        self.records[object_type][record_id]["archived_at"] = at
        self.clock = max(self.clock, at)

    def delete(self, object_type: str, record_id: str) -> None:
        """Delete one record permanently: it leaves every listing, search and batch read, archived ones too."""

        del self.records[object_type][record_id]

    def merge(self, object_type: str, primary: str, *others: str, modified: datetime) -> str:
        """Merge records as HubSpot does and return the new record's id.

        The new record takes ``primary``'s properties and names every merged id in
        ``hs_merged_object_ids``; the merged records leave the account.
        """

        merged = (primary, *others)
        properties = dict(self.records[object_type][primary]["properties"])
        for record_id in merged:
            self.delete(object_type, record_id)
        survivor = self.add(
            object_type, {**properties, "hs_merged_object_ids": ";".join(merged)}, modified=modified
        )
        self.merged[object_type].update(dict.fromkeys(merged, survivor))
        return survivor

    def queue(self, *answers: httpx2.Response | BaseException | None) -> None:
        """Answer the next requests with ``answers``, in order, before any routing; ``None`` lets one through."""

        self.queued.extend(answers)

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one outbound request as the account would, stating :attr:`clock` as its ``Date``."""

        self.requests.append(request)
        if self.queued and (queued := self.queued.pop(0)) is not None:
            if isinstance(queued, BaseException):
                raise queued
            response = queued
        else:
            response = self._route(request)
        response.headers.setdefault("Date", format_datetime(self.clock, usegmt=True))
        return response

    def _route(self, request: httpx2.Request) -> httpx2.Response:
        url = urlsplit(str(request.url))
        if f"{url.scheme}://{url.netloc}" != _HOST:
            return httpx2.Response(404)
        parts = url.path.strip("/").split("/")
        body = json.loads(request.content) if request.content else {}
        query = {name: values[-1] for name, values in parse_qs(url.query).items()}
        if request.method == "POST" and parts == ["oauth", "v2", "private-apps", "get", "access-token-info"]:
            return self._token_info(body)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return error_response(401, category="INVALID_AUTHENTICATION")
        match request.method, parts:
            case "GET", ["account-info", version, "details"] if version == API_VERSION:
                return self._account()
            case "GET", ["crm", "objects", version, object_type] if version == API_VERSION:
                return self._scoped(object_type) or self._list(object_type, query)
            case "POST", ["crm", "objects", version, object_type, "search"] if version == API_VERSION:
                return self._scoped(object_type) or self._search(object_type, body)
            case "POST", ["crm", "objects", version, object_type, "batch", "read"] if version == API_VERSION:
                return self._scoped(object_type) or self._batch_read(object_type, body)
        return httpx2.Response(404, json={"status": "error", "category": "OBJECT_NOT_FOUND"})

    def _stamp(self, object_type: str, record_id: str, modified: datetime, lag: timedelta) -> None:
        record = self.records[object_type][record_id]
        record["modified"], record["searchable"] = modified, modified + lag if lag else None
        record["properties"][_MODIFIED_PROPERTY[object_type]] = _iso(modified)
        self.clock = max(self.clock, modified)

    def _live(self, object_type: str) -> dict[str, dict[str, Any]]:
        return {key: record for key, record in self.records[object_type].items() if record["archived_at"] is None}

    def _scoped(self, object_type: str) -> httpx2.Response | None:
        if object_type not in self.records:
            return httpx2.Response(404, json={"status": "error", "category": "OBJECT_NOT_FOUND"})
        if _READ_SCOPE[object_type] in self.scopes:
            return None
        return error_response(403, category="MISSING_SCOPES")

    def _resource(self, object_type: str, record_id: str, names: Iterable[str]) -> dict[str, Any]:
        record = self.records[object_type][record_id]
        stated = record["properties"]
        defaults = ("hs_object_id", "createdate", _MODIFIED_PROPERTY[object_type])
        defined = self.properties[object_type]
        data = {
            "id": record_id,
            "properties": {name: stated.get(name) for name in (*defaults, *names) if name in defined},
            "createdAt": _iso(record["created"]),
            "updatedAt": _iso(record["modified"]),
            "archived": record["archived_at"] is not None,
        }
        if record["archived_at"] is not None:
            data["archivedAt"] = _iso(record["archived_at"])
        return data

    def _token_info(self, body: Mapping[str, Any]) -> httpx2.Response:
        if body.get("tokenKey") != self.token:
            return error_response(401, category="INVALID_AUTHENTICATION")
        return httpx2.Response(
            200,
            json={
                "hubId": self.hub_id,
                "userId": 7654321,
                "appId": 2345678,
                "scopes": sorted(self.scopes),
                "isUserToken": False,
                "tokenType": "SERVICE_KEY",
            },
        )

    def _account(self) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "portalId": self.hub_id,
                "accountType": "STANDARD",
                "timeZone": "Etc/UTC",
                "companyCurrency": "USD",
                "additionalCurrencies": [],
                "utcOffset": "+00:00",
                "utcOffsetMilliseconds": 0,
                "uiDomain": "app.hubspot.com",
                "dataHostingLocation": "na1",
            },
        )

    def _list(self, object_type: str, query: Mapping[str, str]) -> httpx2.Response:
        limit = int(query.get("limit", "10"))
        if limit > _LIST_PAGE_MAX:
            return error_response(400, category="VALIDATION_ERROR")
        archived = query.get("archived") == "true"
        start = int(query["after"]) if query.get("after") else None
        keys = sorted(
            (
                key
                for key, record in self.records[object_type].items()
                if (record["archived_at"] is not None) == archived and (start is None or int(key) >= start)
            ),
            key=int,
        )
        names = [name for name in query.get("properties", "").split(",") if name]
        body: dict[str, Any] = {"results": [self._resource(object_type, key, names) for key in keys[:limit]]}
        if len(keys) > limit:
            body["paging"] = {"next": {"after": keys[limit], "link": f"?after={keys[limit]}"}}
        return httpx2.Response(200, json=body)

    def _search(self, object_type: str, body: Mapping[str, Any]) -> httpx2.Response:
        searchable = (_MODIFIED_PROPERTY[object_type], _RECORD_ID)
        groups, sorts, limit = body.get("filterGroups", ()), body.get("sorts", ()), int(body.get("limit", 10))
        filters = groups[0].get("filters", ()) if len(groups) == 1 else ()
        if (
            limit > _SEARCH_PAGE_MAX
            or "after" in body
            or len(groups) != 1
            or [sort.get("direction") for sort in sorts] != ["ASCENDING"]
            or sorts[0].get("propertyName") not in searchable
            or any(
                item.get("propertyName") not in searchable or item.get("operator") not in _SEARCH_OPERATORS
                for item in filters
            )
        ):
            return error_response(400, category="VALIDATION_ERROR")

        def value(key: str, name: str) -> int:
            return int(key) if name == _RECORD_ID else _epoch_ms(self.records[object_type][key]["modified"])

        live = self._live(object_type)
        matches = sorted(
            (
                key
                for key, record in live.items()
                if (record["searchable"] is None or record["searchable"] <= self.clock)
                and all(
                    _SEARCH_OPERATORS[item["operator"]](value(key, item["propertyName"]), int(item["value"]))
                    for item in filters
                )
            ),
            key=lambda key: (value(key, sorts[0]["propertyName"]), -int(key)),
        )
        names = body.get("properties", ())
        result: dict[str, Any] = {
            "total": len(matches),
            "results": [self._resource(object_type, key, names) for key in matches[:limit]],
        }
        if len(matches) > limit:
            result["paging"] = {"next": {"after": str(limit)}}
        return httpx2.Response(200, json=result)

    def _batch_read(self, object_type: str, body: Mapping[str, Any]) -> httpx2.Response:
        keys = [str(item["id"]) for item in body.get("inputs", ())]
        if len(keys) > _BATCH_READ_MAX:
            return error_response(400, category="VALIDATION_ERROR")
        live, names = self._live(object_type), body.get("properties", ())
        found: dict[str, dict[str, Any]] = {}
        errors: dict[str, list[str]] = {}
        for key in keys:
            target = key
            while self.resolves_merged and target not in live and target in self.merged[object_type]:
                target = self.merged[object_type][target]
            if key in self.read_errors:
                errors.setdefault(self.read_errors[key], []).append(key)
            elif target in live:
                found.setdefault(target, self._resource(object_type, target, names))
            else:
                errors.setdefault("OBJECT_NOT_FOUND", []).append(key)
        result: dict[str, Any] = {"status": "COMPLETE", "results": list(found.values())}
        if errors:
            result |= {
                "numErrors": len(errors),
                "errors": [
                    {"status": "error", "category": category, "message": VENDOR_TEXT, "context": {"ids": ids}}
                    for category, ids in errors.items()
                ],
            }
        return httpx2.Response(207 if errors else 200, json=result)


@contextmanager
def serve(server: HubSpotServer) -> Iterator[HubSpotServer]:
    """Answer every outbound request from ``server`` while the context is open."""

    with mock_transport(server.answer):
        yield server


@pytest.fixture
def hubspot_server() -> Iterator[HubSpotServer]:
    """A served, empty scripted HubSpot account that accepts :data:`TOKEN`."""

    with serve(HubSpotServer()) as server:
        yield server


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch_ms(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(milliseconds=1)
