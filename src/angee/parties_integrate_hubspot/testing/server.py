"""A scripted HubSpot account for tests of HubSpot sync."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from angee.integrate.testing.http import mock_transport
from angee.parties_integrate_hubspot.client import (
    API_VERSION,
    ASSOCIATION_BATCH_LIMIT,
    BATCH_READ_LIMIT,
    HUBSPOT_API,
    LIST_PAGE_LIMIT,
    MODIFIED_PROPERTY,
    SEARCH_PAGE_LIMIT,
    SEARCH_RESULT_LIMIT,
    decode_datetime,
    read_scope,
)

TOKEN = "pat-na1-00000000-0000-4000-8000-000000000000"
"""The invented service key the scripted account accepts."""
HUB_ID = 1234567
"""The scripted account's hub (portal) id."""
CREATED = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
"""The creation time a record states unless ``add`` names one."""
CONTACT_TO_COMPANY = 279
"""HubSpot's unlabelled contact-to-company association type."""
PRIMARY_COMPANY = 1
"""HubSpot's contact-to-company association type labelled ``Primary``."""

_DEFINITIONS = {
    "contacts": (
        ("firstname", "string", "text"),
        ("lastname", "string", "text"),
        ("email", "string", "text"),
        ("hs_additional_emails", "enumeration", "checkbox"),
        ("lastmodifieddate", "datetime", "date"),
    ),
    "companies": (("name", "string", "text"), ("hs_lastmodifieddate", "datetime", "date")),
}


def error_response(
    status: int,
    *,
    category: str | None = None,
    policy_name: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> httpx2.Response:
    """Return one HubSpot error answer; its ``message`` is vendor text a client must never repeat."""

    body: dict[str, Any] = {
        "status": "error",
        "message": "Vendor text that must not reach an operator.",
        "correlationId": "00000000-0000-4000-8000-000000000001",
    }
    if category:
        body["category"] = category
    if policy_name:
        body |= {"errorType": "RATE_LIMIT", "policyName": policy_name}
    return httpx2.Response(status, json=body, headers=headers)


class HubSpotServer:
    """One HubSpot account in memory, which ``serve`` makes every outbound request reach.

    ``records`` maps each object type (``contacts``, ``companies``) to its records
    by numeric id; ``add``, ``update``, ``archive`` and ``merge`` change them as
    HubSpot would, stamping the type's modified property. The account answers token
    introspection, account details, property definitions, list pages (by id,
    ``archived`` lists the soft-deleted), modified-time searches (ascending, offset
    paging, refused at ``search_ceiling``), batch reads (207 with the missing ids)
    and contact-to-company association batch reads (``association_page_size`` per
    record and page). It refuses a wrong bearer with 401 and a missing read scope
    with 403 ``MISSING_SCOPES``.

    Server-side behaviour is scripted rather than patched: ``queue`` answers the
    next requests before any routing (an exception models an unreachable host),
    ``unindexed`` ids stay out of search results as HubSpot's lagging index does,
    and ``resolves_merged`` makes a batch read of a merged id return the record it
    was merged into, under that record's id. Every request is recorded in
    ``requests``.
    """

    def __init__(
        self,
        *,
        scopes: Iterable[str] = (read_scope("contacts"), read_scope("companies")),
        search_ceiling: int = SEARCH_RESULT_LIMIT,
        association_page_size: int = 500,
    ) -> None:
        """Serve an empty account; nothing is recorded until a client sends a request."""

        self.token = TOKEN
        self.hub_id = HUB_ID
        self.scopes = set(scopes)
        self.search_ceiling = search_ceiling
        self.association_page_size = association_page_size
        self.records: dict[str, dict[str, dict[str, Any]]] = {object_type: {} for object_type in MODIFIED_PROPERTY}
        self.definitions: dict[str, list[dict[str, Any]]] = {
            object_type: [_definition(name, kind, field, hubspot_defined=True) for name, kind, field in rows]
            for object_type, rows in _DEFINITIONS.items()
        }
        self.associations: dict[str, list[dict[str, Any]]] = {}
        self.merged: dict[str, dict[str, str]] = {object_type: {} for object_type in MODIFIED_PROPERTY}
        self.resolves_merged = True
        self.unindexed: set[str] = set()
        self.queued: list[httpx2.Response | BaseException] = []
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
    ) -> str:
        """Create one record stating ``properties`` and return its id (the next free one unless named)."""

        record_id = record_id or str(self._next_id)
        self._next_id = max(self._next_id, int(record_id) + 1)
        self.records[object_type][record_id] = {
            "properties": {**(properties or {}), "hs_object_id": record_id, "createdate": _iso(created)},
            "created": created,
            "archived_at": None,
        }
        self._stamp(object_type, record_id, modified)
        return record_id

    def update(
        self, object_type: str, record_id: str, properties: Mapping[str, str | None], *, modified: datetime
    ) -> None:
        """Change ``properties`` of one record and stamp ``modified`` on it."""

        self.records[object_type][record_id]["properties"].update(properties)
        self._stamp(object_type, record_id, modified)

    def archive(self, object_type: str, record_id: str, *, at: datetime) -> None:
        """Soft-delete one record: it leaves lists, searches and batch reads, and lists as archived."""

        self.records[object_type][record_id]["archived_at"] = at

    def merge(self, object_type: str, primary: str, *others: str, modified: datetime) -> str:
        """Merge records as HubSpot does and return the new record's id.

        The new record takes ``primary``'s properties and names every merged id in
        ``hs_merged_object_ids``; the merged records leave the account.
        """

        merged = (primary, *others)
        properties = dict(self.records[object_type][primary]["properties"])
        for record_id in merged:
            del self.records[object_type][record_id]
        survivor = self.add(
            object_type, {**properties, "hs_merged_object_ids": ";".join(merged)}, modified=modified
        )
        self.merged[object_type].update(dict.fromkeys(merged, survivor))
        return survivor

    def associate(self, contact_id: str, company_id: str, *, primary: bool = False) -> None:
        """Associate a contact with a company, labelled ``Primary`` when ``primary``."""

        types = [{"category": "HUBSPOT_DEFINED", "typeId": CONTACT_TO_COMPANY, "label": None}]
        if primary:
            types.append({"category": "HUBSPOT_DEFINED", "typeId": PRIMARY_COMPANY, "label": "Primary"})
        self.associations.setdefault(contact_id, []).append({"toObjectId": int(company_id), "associationTypes": types})

    def define(self, object_type: str, name: str, *, kind: str, field_type: str, options: Iterable[str] = ()) -> None:
        """Add a custom property definition of HubSpot ``kind`` (its ``type``)."""

        self.definitions[object_type].append(_definition(name, kind, field_type, options=options))

    def queue(self, *answers: httpx2.Response | BaseException) -> None:
        """Answer the next requests with ``answers``, in order, before any routing."""

        self.queued.extend(answers)

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one outbound request as the account would."""

        self.requests.append(request)
        if self.queued:
            queued = self.queued.pop(0)
            if isinstance(queued, BaseException):
                raise queued
            return queued
        url = urlsplit(str(request.url))
        if f"{url.scheme}://{url.netloc}" != HUBSPOT_API:
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
            case "GET", ["crm", "properties", version, object_type] if version == API_VERSION:
                return self._scoped(object_type) or self._properties(object_type)
            case "GET", ["crm", "objects", version, object_type] if version == API_VERSION:
                return self._scoped(object_type) or self._list(object_type, query)
            case "POST", ["crm", "objects", version, object_type, "search"] if version == API_VERSION:
                return self._scoped(object_type) or self._search(object_type, body)
            case "POST", ["crm", "objects", version, object_type, "batch", "read"] if version == API_VERSION:
                return self._scoped(object_type) or self._batch_read(object_type, body)
            case "POST", ["crm", "associations", version, "contacts", "companies", "batch", "read"] if (
                version == API_VERSION
            ):
                return self._scoped("contacts", "companies") or self._associations(body)
        return httpx2.Response(404, json={"status": "error", "category": "OBJECT_NOT_FOUND"})

    def _stamp(self, object_type: str, record_id: str, modified: datetime) -> None:
        self.records[object_type][record_id]["properties"][MODIFIED_PROPERTY[object_type]] = _iso(modified)

    def _modified(self, object_type: str, record: Mapping[str, Any]) -> datetime:
        moment = decode_datetime(record["properties"][MODIFIED_PROPERTY[object_type]])
        assert moment is not None, "every scripted record states its modified time"
        return moment

    def _live(self, object_type: str) -> dict[str, dict[str, Any]]:
        return {key: record for key, record in self.records[object_type].items() if record["archived_at"] is None}

    def _scoped(self, *object_types: str) -> httpx2.Response | None:
        if object_types[0] not in self.records:
            return httpx2.Response(404, json={"status": "error", "category": "OBJECT_NOT_FOUND"})
        if all(read_scope(object_type) in self.scopes for object_type in object_types):
            return None
        return error_response(403, category="MISSING_SCOPES")

    def _resource(self, object_type: str, record_id: str, names: Iterable[str]) -> dict[str, Any]:
        record = self.records[object_type][record_id]
        stated = record["properties"]
        defaults = ("hs_object_id", "createdate", MODIFIED_PROPERTY[object_type])
        data = {
            "id": record_id,
            "properties": {name: stated.get(name) for name in (*defaults, *names)},
            "createdAt": _iso(record["created"]),
            "updatedAt": stated[MODIFIED_PROPERTY[object_type]],
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

    def _properties(self, object_type: str) -> httpx2.Response:
        return httpx2.Response(200, json={"results": self.definitions[object_type]})

    def _list(self, object_type: str, query: Mapping[str, str]) -> httpx2.Response:
        limit = int(query.get("limit", "10"))
        if limit > LIST_PAGE_LIMIT:
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
        modified_property = MODIFIED_PROPERTY[object_type]
        filters = [item for group in body.get("filterGroups", ()) for item in group.get("filters", ())]
        limit, offset = int(body.get("limit", 10)), int(body.get("after") or 0)
        if (
            limit > SEARCH_PAGE_LIMIT
            or offset >= self.search_ceiling
            or len(filters) != 1
            or filters[0].get("propertyName") != modified_property
            or filters[0].get("operator") != "GTE"
            or body.get("sorts") != [{"propertyName": modified_property, "direction": "ASCENDING"}]
        ):
            return error_response(400, category="VALIDATION_ERROR")
        since = decode_datetime(filters[0]["value"])
        assert since is not None, "the modified filter states a time"
        matches = sorted(
            (self._modified(object_type, record), int(key), key)
            for key, record in self._live(object_type).items()
            if key not in self.unindexed and self._modified(object_type, record) >= since
        )
        window = matches[offset : min(offset + limit, self.search_ceiling)]
        names = body.get("properties", ())
        result: dict[str, Any] = {
            "total": len(matches),
            "results": [self._resource(object_type, key, names) for *_, key in window],
        }
        if offset + limit < len(matches):
            result["paging"] = {"next": {"after": str(offset + limit)}}
        return httpx2.Response(200, json=result)

    def _batch_read(self, object_type: str, body: Mapping[str, Any]) -> httpx2.Response:
        keys = [str(item["id"]) for item in body.get("inputs", ())]
        if len(keys) > BATCH_READ_LIMIT:
            return error_response(400, category="VALIDATION_ERROR")
        live, names = self._live(object_type), body.get("properties", ())
        found: dict[str, dict[str, Any]] = {}
        missing = []
        for key in keys:
            target = key
            while self.resolves_merged and target not in live and target in self.merged[object_type]:
                target = self.merged[object_type][target]
            if target in live:
                found.setdefault(target, self._resource(object_type, target, names))
            else:
                missing.append(key)
        result: dict[str, Any] = {"status": "COMPLETE", "results": list(found.values())}
        if missing:
            result |= {
                "numErrors": 1,
                "errors": [{"status": "error", "category": "OBJECT_NOT_FOUND", "context": {"ids": missing}}],
            }
        return httpx2.Response(207 if missing else 200, json=result)

    def _associations(self, body: Mapping[str, Any]) -> httpx2.Response:
        inputs = body.get("inputs", ())
        if len(inputs) > ASSOCIATION_BATCH_LIMIT:
            return error_response(400, category="VALIDATION_ERROR")
        size = self.association_page_size
        results, missing = [], []
        for item in inputs:
            key, offset = str(item["id"]), int(item.get("after") or 0)
            targets = self.associations.get(key, [])
            if not targets:
                missing.append(key)
                continue
            entry: dict[str, Any] = {"from": {"id": key}, "to": targets[offset : offset + size]}
            if offset + size < len(targets):
                entry["paging"] = {"next": {"after": str(offset + size)}}
            results.append(entry)
        result: dict[str, Any] = {"status": "COMPLETE", "results": results}
        if missing:
            result |= {
                "numErrors": 1,
                "errors": [{"status": "error", "category": "NO_ASSOCIATIONS_FOUND", "context": {"ids": missing}}],
            }
        return httpx2.Response(207 if missing else 200, json=result)


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


def _definition(
    name: str, kind: str, field_type: str, *, options: Iterable[str] = (), hubspot_defined: bool = False
) -> dict[str, Any]:
    return {
        "name": name,
        "label": name.replace("_", " ").capitalize(),
        "type": kind,
        "fieldType": field_type,
        "options": [{"value": value, "label": value.capitalize(), "hidden": False} for value in options],
        "hubspotDefined": hubspot_defined,
        "calculated": False,
        "externalOptions": False,
    }


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
