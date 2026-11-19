"""HubSpot's CRM REST API at the date-versioned ``2026-09`` paths, over integrate's HTTP owner.

The one owner of the HubSpot wire protocol for this addon. Requests go through
``angee.integrate.http.HttpClient`` (SSRF-pinned, byte-capped) with the
authorization headers the caller supplies; the client never reads a credential.
Every answer is validated into a pydantic model that ignores the fields it does
not name, so a new HubSpot field never breaks a sync and a missing required one
is a :class:`HubSpotError`. No HubSpot client library is involved.

A refusal becomes a :class:`HubSpotError` composed from facts: the status,
HubSpot's error ``category`` and rate-limit ``policyName``, and the scopes the
request needed, never HubSpot's message text. :func:`_retry_policy` is the status
table that decides which refusals a later attempt can outlive and how long to
wait. A failure to reach HubSpot, and an oversized answer, are transient.

HubSpot states every property value as a nullable string; the ``parse_*``
functions turn one into a Python value by property type. Which property states
a record's last-modified time depends on its object type, and the client alone
knows it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Any
from urllib.parse import urlencode

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import HttpClient, ResponseTooLargeError, retry_after

HUBSPOT_API = "https://api.hubapi.com"
API_VERSION = "2026-09"
"""The one date-based API version every path names."""

LIST_PAGE_LIMIT = 100
"""The most records one list page returns."""
SEARCH_PAGE_LIMIT = 200
"""The most records one search page returns."""
BATCH_READ_LIMIT = 100
"""The most ids one batch read takes."""

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""HubSpot's timestamps count milliseconds from here."""

_MODIFIED_PROPERTY = {"contacts": "lastmodifieddate"}
_RECORD_ID = "hs_object_id"
_NOT_FOUND = "OBJECT_NOT_FOUND"
_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 30
_LOCKED_WAIT = timedelta(seconds=2)
_RATE_LIMIT_WAITS = {"TEN_SECONDLY_ROLLING": timedelta(seconds=10), "DAILY": timedelta(hours=1)}


def get_read_scope(object_type: str) -> str:
    """Return the scope a service key needs to read records of a standard ``object_type``."""

    return f"crm.objects.{object_type}.read"


class HubSpotError(IntegrationError):
    """A HubSpot refusal whose message is composed from facts, never from HubSpot's text.

    ``status`` is the HTTP status of the refused answer, or ``None`` when the
    failure is not a refused answer (unreachable, oversized, unreadable, or a
    record HubSpot could not read).
    """

    def __init__(
        self,
        public_message: str,
        *,
        status: int | None = None,
        transient: bool | None = None,
        retry_after: timedelta | None = None,
    ) -> None:
        """Record the refusal's status beside integrate's transience and wait."""

        super().__init__(public_message, transient=transient, retry_after=retry_after)
        self.status = status

    @classmethod
    def from_response(cls, response: httpx2.Response, *, scopes: Sequence[str] = ()) -> HubSpotError:
        """Compose the refusal ``response`` states; ``scopes`` are what the request needed."""

        body = _error_body(response)
        transient, wait = _retry_policy(response, body)
        status = response.status_code
        if status == 403 and body.category == "MISSING_SCOPES":
            return cls.missing_scopes(scopes, status=status)
        if status == 401:
            message = "HubSpot rejected the access token. Enter a valid HubSpot service key."
        elif status == 403:
            message = "HubSpot refused this request for the service key."
        elif status == 423:
            message = "HubSpot has locked these records for a moment."
        elif status == 429 and body.policy_name == "DAILY":
            message = "This HubSpot account has used its daily API request limit."
        elif status == 429:
            message = "HubSpot is rate limiting this account."
        elif transient:
            message = "HubSpot is temporarily unavailable."
        else:
            message = f"HubSpot refused the request (HTTP {status})."
        return cls(message, status=status, transient=transient, retry_after=wait)

    @classmethod
    def missing_scopes(cls, scopes: Sequence[str], *, status: int | None = None) -> HubSpotError:
        """Refuse a service key that lacks ``scopes``, which this connection needs; permanent.

        ``status`` is 403 when HubSpot refused a request for it, ``None`` when the
        key's introspection showed the scopes it lacks.
        """

        needed = ", ".join(scopes) or "the scope this request needs"
        return cls(f"The HubSpot service key is missing a scope this connection needs: {needed}.", status=status)


class _HubSpotModel(BaseModel):
    """HubSpot's camelCase JSON read into snake_case fields; unknown fields are ignored."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_alias=True, validate_by_name=True, extra="ignore", frozen=True
    )


class _HubSpotErrorBody(_HubSpotModel):
    """The facts of a HubSpot error answer a refusal is composed from."""

    category: str | None = None
    policy_name: str | None = None


class HubSpotTokenInfo(_HubSpotModel):
    """What HubSpot's token introspection states about one access token: the scopes it grants."""

    scopes: tuple[str, ...] = ()


class HubSpotAccount(_HubSpotModel):
    """The account (portal, or hub) a token belongs to, and the host its web app serves it from."""

    portal_id: int
    ui_domain: str = Field(pattern=r"^[A-Za-z0-9.-]+$")


class HubSpotObject(_HubSpotModel):
    """One CRM record; HubSpot states every property value as a nullable string."""

    id: str
    properties: dict[str, str | None] = Field(default_factory=dict)

    def modified_at(self, object_type: str) -> datetime | None:
        """Return the record's last-modified time, or ``None`` when it was not read or cannot be parsed."""

        try:
            return _parse_datetime(self.properties.get(_MODIFIED_PROPERTY[object_type]))
        except ValueError:
            return None


class HubSpotNext(_HubSpotModel):
    """The position a following page starts from."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    after: str


class HubSpotPaging(_HubSpotModel):
    """A paged answer's link to its following page, absent on the last one."""

    next: HubSpotNext | None = None


class HubSpotPage(_HubSpotModel):
    """One page of a list walk or a search.

    A list page's following position is the id of the record it starts at; a
    search page's is an offset into the query's results, which this client reads
    only as a sign that more results follow.
    """

    results: tuple[HubSpotObject, ...] = ()
    paging: HubSpotPaging | None = None

    @property
    def next_after(self) -> str:
        """Return the following page's position, or ``""`` on the last page."""

        return self.paging.next.after if self.paging is not None and self.paging.next is not None else ""

    def head(self, count: int) -> HubSpotPage:
        """Return the page cut to its first ``count`` records, the following page starting at the first one cut.

        That position is the cut record's id, where a list walk resumes; a search
        page's position is read only as a sign that more results follow.
        """

        if count >= len(self.results):
            return self
        following = HubSpotPaging(next=HubSpotNext(after=self.results[count].id))
        return HubSpotPage(results=self.results[:count], paging=following)


class _HubSpotBatchError(_HubSpotModel):
    """One error a partial (207) batch answer states for some of its records."""

    category: str | None = None


class _HubSpotBatchAnswer(_HubSpotModel):
    results: tuple[HubSpotObject, ...] = ()
    errors: tuple[_HubSpotBatchError, ...] = ()


@dataclass(frozen=True, slots=True)
class HubSpotBatchRead:
    """The records a batch read found, and the requested ids HubSpot has no record under.

    A found record may carry another id than the one requested: a merge replaces
    the merged ids with its surviving record's, so a merged id is missing beside
    its survivor.
    """

    found: tuple[HubSpotObject, ...]
    missing: tuple[str, ...]


class HubSpotClient:
    """One HubSpot account's CRM, read with the authorization headers its caller supplies.

    The caller owns the credential: it passes the credential's own authorization
    headers, and the key itself to :meth:`token_info`. Each method sends only the
    requests its docstring names; nothing is cached between calls.
    """

    def __init__(self, headers: Mapping[str, str]) -> None:
        """Bind the authorization headers every request carries."""

        self.headers = dict(headers)
        self.http = HttpClient()

    def token_info(self, token_key: str) -> HubSpotTokenInfo:
        """Introspect ``token_key``: the scopes it grants.

        HubSpot documents introspection for private-app keys only; a caller that is
        refused learns what it needs from the requests the key makes instead.
        """

        return self._request(
            HubSpotTokenInfo, "POST", "/oauth/v2/private-apps/get/access-token-info", payload={"tokenKey": token_key}
        )

    def account(self) -> HubSpotAccount:
        """Return the account the token belongs to."""

        return self._request(HubSpotAccount, "GET", f"/account-info/{API_VERSION}/details")

    def list_page(
        self, object_type: str, *, after: str, limit: int, properties: Sequence[str], archived: bool = False
    ) -> HubSpotPage:
        """Return one page of ``object_type``'s records in id order, starting at the record ``after`` names.

        ``properties`` and the type's modified time come back, besides HubSpot's
        defaults. ``archived`` lists the soft-deleted records instead of the live ones.
        """

        query = {
            "limit": str(limit),
            "archived": "true" if archived else "false",
            "properties": ",".join(_requested(object_type, properties)),
        }
        if after:
            query["after"] = after
        path = f"/crm/objects/{API_VERSION}/{object_type}"
        return self._request(HubSpotPage, "GET", path, query=query, scopes=(get_read_scope(object_type),))

    def record_ids(self, object_type: str, *, after: str | None = None, limit: int = LIST_PAGE_LIMIT) -> Iterator[str]:
        """Yield the ids of ``object_type``'s live records strictly after ``after``, in HubSpot's id order.

        HubSpot lists records by numeric id, and a list page's ``after`` names the
        record it starts at. The walk starts at ``after`` itself and drops the ids
        up to it, so it seeks past ``after`` whether or not that record still
        exists, without reading the records before it. Each page is requested as
        the caller reaches it.
        """

        floor = int(after) if after else None
        position = after or ""
        while True:
            page = self.list_page(object_type, after=position, limit=limit, properties=())
            yield from (record.id for record in page.results if floor is None or int(record.id) > floor)
            if not page.next_after:
                return
            if page.next_after == position:
                raise HubSpotError("HubSpot did not advance its record listing.")
            position = page.next_after

    def search_modified(
        self, object_type: str, *, since: datetime, limit: int, properties: Sequence[str]
    ) -> HubSpotPage:
        """Return the first ``limit`` records of ``object_type`` modified strictly after ``since``, oldest change first.

        HubSpot does not state how it orders records that share one modified time.
        """

        modified = _MODIFIED_PROPERTY[object_type]
        changed = {"propertyName": modified, "operator": "GT", "value": str(_epoch_ms(since))}
        return self._search(object_type, [changed], sort=modified, limit=limit, properties=properties)

    def search_modified_at(
        self, object_type: str, *, moment: datetime, after: str, limit: int, properties: Sequence[str]
    ) -> HubSpotPage:
        """Return the first ``limit`` records of ``object_type`` modified at exactly ``moment``, in id order.

        Only records with an id above ``after`` are returned, every one when it is ``""``.
        """

        modified = _MODIFIED_PROPERTY[object_type]
        filters = [{"propertyName": modified, "operator": "EQ", "value": str(_epoch_ms(moment))}]
        if after:
            filters.append({"propertyName": _RECORD_ID, "operator": "GT", "value": after})
        return self._search(object_type, filters, sort=_RECORD_ID, limit=limit, properties=properties)

    def batch_read(self, object_type: str, ids: Sequence[str], properties: Sequence[str]) -> HubSpotBatchRead:
        """Read ``ids`` in batches of :data:`BATCH_READ_LIMIT`.

        An id is missing when HubSpot states it has no such record, or answers it
        under another id: a merge's survivor. Any other error HubSpot states for a
        record is a transient :class:`HubSpotError`, so a record HubSpot failed to
        read is never reported missing.
        """

        path = f"/crm/objects/{API_VERSION}/{object_type}/batch/read"
        requested = tuple(dict.fromkeys(ids))
        found: list[HubSpotObject] = []
        for chunk in batched(requested, BATCH_READ_LIMIT):
            payload = {
                "inputs": [{"id": key} for key in chunk],
                "properties": list(properties),
                "propertiesWithHistory": [],
            }
            answer = self._request(
                _HubSpotBatchAnswer, "POST", path, payload=payload, scopes=(get_read_scope(object_type),)
            )
            if failed := next((error for error in answer.errors if error.category != _NOT_FOUND), None):
                stated = f" ({failed.category})" if failed.category else ""
                raise HubSpotError(f"HubSpot could not read some records{stated}.", transient=True)
            found.extend(answer.results)
        answered = {record.id for record in found}
        return HubSpotBatchRead(found=tuple(found), missing=tuple(key for key in requested if key not in answered))

    def _search(
        self, object_type: str, filters: list[dict[str, str]], *, sort: str, limit: int, properties: Sequence[str]
    ) -> HubSpotPage:
        """Return the first page of the records every one of ``filters`` holds for, ascending on ``sort``.

        HubSpot takes one sort rule and requires every filter of a group to hold.
        The type's modified time is always read back, so each record states the
        time it was found by. HubSpot's search lags its writes and never returns
        archived records.
        """

        payload = {
            "filterGroups": [{"filters": filters}],
            "sorts": [{"propertyName": sort, "direction": "ASCENDING"}],
            "properties": _requested(object_type, properties),
            "limit": limit,
        }
        path = f"/crm/objects/{API_VERSION}/{object_type}/search"
        return self._request(HubSpotPage, "POST", path, payload=payload, scopes=(get_read_scope(object_type),))

    def _request[T: BaseModel](
        self,
        model: type[T],
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        scopes: Sequence[str] = (),
    ) -> T:
        """Send one request and validate its answer into ``model``; any refusal is a :class:`HubSpotError`."""

        url = f"{HUBSPOT_API}{path}" + (f"?{urlencode(query)}" if query else "")
        headers = {**self.headers, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self.http.request(
                method,
                url,
                headers=headers,
                body=None if payload is None else json.dumps(payload).encode(),
                max_bytes=_RESPONSE_MAX_BYTES,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except (httpx2.TransportError, RequestNotSentError) as error:
            raise HubSpotError("HubSpot could not be reached.", transient=True) from error
        except ResponseTooLargeError as error:
            raise HubSpotError("HubSpot returned an oversized response.", transient=True) from error
        if not response.is_success:
            raise HubSpotError.from_response(response, scopes=scopes)
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            raise HubSpotError("HubSpot returned a response this connection cannot read.") from error


def _requested(object_type: str, properties: Sequence[str]) -> list[str]:
    """Return ``properties`` with ``object_type``'s modified property, which every read needs."""

    return list(dict.fromkeys((*properties, _MODIFIED_PROPERTY[object_type])))


def _retry_policy(response: httpx2.Response, body: _HubSpotErrorBody) -> tuple[bool, timedelta | None]:
    """HubSpot's status table: whether a later attempt can outlive a refusal, and the wait before it.

    423, 429 and every 5xx are transient; every other refusal is permanent. 423
    waits at least two seconds. 429 waits for ``Retry-After`` when HubSpot sends
    it, else for its rate-limit policy's window (ten seconds, or an hour for the
    daily limit). A 5xx waits for ``Retry-After`` when sent.
    """

    status = response.status_code
    if status not in (423, 429) and status < 500:
        return False, None
    hinted = retry_after(response)
    if status == 423:
        return True, max(hinted or _LOCKED_WAIT, _LOCKED_WAIT)
    if status == 429:
        policy = _RATE_LIMIT_WAITS.get(body.policy_name or "", _RATE_LIMIT_WAITS["TEN_SECONDLY_ROLLING"])
        return True, hinted or policy
    return True, hinted


def _error_body(response: httpx2.Response) -> _HubSpotErrorBody:
    """Read the facts of a refusal's body; an unreadable body states none."""

    try:
        return _HubSpotErrorBody.model_validate_json(response.content)
    except ValidationError:
        return _HubSpotErrorBody()


def _epoch_ms(moment: datetime) -> int:
    """Return ``moment`` as HubSpot's epoch milliseconds; a naive time is UTC."""

    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return (aware - EPOCH) // timedelta(milliseconds=1)


def parse_string(value: str | None) -> str:
    """Return a text property's value, or a single-select enumeration's option value; ``""`` when unset."""

    return (value or "").strip()


def parse_options(value: str | None) -> tuple[str, ...]:
    """Return the values of a multi-valued property, which HubSpot joins with ``;``."""

    return tuple(part for part in (item.strip() for item in (value or "").split(";")) if part)


def _parse_datetime(value: str | None) -> datetime | None:
    """Return a ``datetime`` property's instant in UTC, stated in ISO 8601 or epoch milliseconds.

    ``None`` when unset; a time without an offset is UTC; ``ValueError`` when the
    value is neither form or names an instant outside the calendar.
    """

    text = parse_string(value)
    if not text:
        return None
    try:
        if text.removeprefix("-").isdecimal():
            return EPOCH + timedelta(milliseconds=int(text))
        moment = datetime.fromisoformat(text)
        return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise ValueError("Not a HubSpot date or time value.") from error
