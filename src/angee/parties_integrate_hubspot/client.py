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

HubSpot states every property value as a nullable string; the ``decode_*``
functions turn one into a Python value by property type.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
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
SEARCH_RESULT_LIMIT = 10_000
"""The most results one search query pages through; HubSpot refuses a page past it."""
BATCH_READ_LIMIT = 100
"""The most ids one batch read takes."""
ASSOCIATION_BATCH_LIMIT = 1_000
"""The most records one association batch read takes."""

MODIFIED_PROPERTY = {"contacts": "lastmodifieddate", "companies": "hs_lastmodifieddate"}
"""The property each object type's last-modified time is searched and read by."""

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""HubSpot's timestamps count milliseconds from here."""

_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 30
_TRANSIENT_STATUSES = frozenset({423, 429, 502, 503, 504, 521, 523, 524})
_LOCKED_WAIT = timedelta(seconds=2)
_RATE_LIMIT_WAITS = {"TEN_SECONDLY_ROLLING": timedelta(seconds=10), "DAILY": timedelta(hours=1)}


def read_scope(object_type: str) -> str:
    """Return the scope a service key needs to read records of a standard ``object_type``."""

    return f"crm.objects.{object_type}.read"


class HubSpotError(IntegrationError):
    """A HubSpot refusal whose message is composed from facts, never from HubSpot's text.

    ``status`` is the HTTP status HubSpot answered with, or ``None`` when no usable
    answer arrived (unreachable, oversized, unreadable).
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
        if status == 401:
            message = "HubSpot rejected the access token. Enter a valid HubSpot service key."
        elif status == 403 and body.category == "MISSING_SCOPES":
            needed = ", ".join(scopes) or "the scope this request needs"
            message = f"The HubSpot service key is missing a scope this connection needs: {needed}."
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
    """What HubSpot's token introspection states about one access token."""

    hub_id: int
    user_id: int | None = None
    app_id: int | None = None
    scopes: tuple[str, ...] = ()
    is_user_token: bool | None = None


class HubSpotAccount(_HubSpotModel):
    """The details HubSpot states for the account (portal) a token belongs to."""

    portal_id: int
    account_type: str | None = None
    time_zone: str | None = None
    company_currency: str | None = None
    data_hosting_location: str | None = None
    ui_domain: str | None = None


class HubSpotOption(_HubSpotModel):
    """One option of an enumeration property: the stored value and its label."""

    value: str
    label: str | None = None


class HubSpotProperty(_HubSpotModel):
    """One property definition: its name, and the type and field type its values decode by."""

    name: str
    label: str | None = None
    type: str | None = None
    field_type: str | None = None
    options: tuple[HubSpotOption, ...] = ()
    hubspot_defined: bool | None = None
    calculated: bool | None = None
    external_options: bool | None = None


class _HubSpotProperties(_HubSpotModel):
    results: tuple[HubSpotProperty, ...] = ()


class HubSpotObject(_HubSpotModel):
    """One CRM record; HubSpot states every property value as a nullable string."""

    id: str
    properties: dict[str, str | None] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    archived: bool = False
    archived_at: datetime | None = None

    def modified_at(self, modified_property: str) -> datetime | None:
        """Return the last-modified time ``modified_property`` states, or ``None`` when it was not read."""

        return decode_datetime(self.properties.get(modified_property))


class HubSpotNext(_HubSpotModel):
    """The position a following page starts from."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    after: str


class HubSpotPaging(_HubSpotModel):
    """A paged answer's link to its following page, absent on the last one."""

    next: HubSpotNext | None = None


class _Paged(_HubSpotModel):
    paging: HubSpotPaging | None = None

    @property
    def next_after(self) -> str:
        """Return the following page's ``after`` position, or ``""`` on the last page."""

        return self.paging.next.after if self.paging is not None and self.paging.next is not None else ""


class HubSpotPage(_Paged):
    """One page of a list walk; ``after`` names the following page's first record id."""

    results: tuple[HubSpotObject, ...] = ()


class HubSpotSearchPage(HubSpotPage):
    """One page of a search; ``after`` is an offset into the query's results."""

    total: int = 0


class HubSpotBatchRead(_HubSpotModel):
    """The records a batch read returned, and the requested ids it did not return.

    A returned record may carry another id than the one requested: a merge
    replaces the merged ids with its surviving record's.
    """

    results: tuple[HubSpotObject, ...] = ()
    missing: tuple[str, ...] = ()


class HubSpotObjectRef(_HubSpotModel):
    """A record named by its id alone."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    id: str


class HubSpotAssociationType(_HubSpotModel):
    """One kind of association: HubSpot's category, its type id and its label (``Primary``)."""

    category: str | None = None
    type_id: int
    label: str | None = None


class HubSpotAssociation(_HubSpotModel):
    """One associated record and the association types that link it."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    to_object_id: str
    association_types: tuple[HubSpotAssociationType, ...] = ()


class _HubSpotAssociations(_Paged):
    from_: HubSpotObjectRef = Field(alias="from")
    to: tuple[HubSpotAssociation, ...] = ()


class _HubSpotAssociationPage(_HubSpotModel):
    results: tuple[_HubSpotAssociations, ...] = ()


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
        """Introspect ``token_key``: its account (hub) id and granted scopes.

        HubSpot documents introspection for private-app keys only; a caller that is
        refused can read :meth:`account` instead.
        """

        return self._request(
            HubSpotTokenInfo, "POST", "/oauth/v2/private-apps/get/access-token-info", payload={"tokenKey": token_key}
        )

    def account(self) -> HubSpotAccount:
        """Return the details of the account the token belongs to."""

        return self._request(HubSpotAccount, "GET", f"/account-info/{API_VERSION}/details")

    def properties(self, object_type: str) -> tuple[HubSpotProperty, ...]:
        """Return ``object_type``'s property definitions, standard and custom."""

        path = f"/crm/properties/{API_VERSION}/{object_type}"
        return self._request(_HubSpotProperties, "GET", path, scopes=(read_scope(object_type),)).results

    def list_page(
        self, object_type: str, *, after: str, limit: int, properties: Sequence[str], archived: bool = False
    ) -> HubSpotPage:
        """Return one page of ``object_type``'s records in id order, starting at the record ``after`` names.

        Only ``properties`` come back, besides HubSpot's defaults. ``archived``
        lists the soft-deleted records instead of the live ones.
        """

        query = {"limit": str(limit), "archived": "true" if archived else "false"}
        if after:
            query["after"] = after
        if properties:
            query["properties"] = ",".join(properties)
        path = f"/crm/objects/{API_VERSION}/{object_type}"
        return self._request(HubSpotPage, "GET", path, query=query, scopes=(read_scope(object_type),))

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
            page = self.list_page(object_type, after=position, limit=limit, properties=("hs_object_id",))
            yield from (record.id for record in page.results if floor is None or int(record.id) > floor)
            if not page.next_after:
                return
            if page.next_after == position:
                raise HubSpotError("HubSpot did not advance its record listing.")
            position = page.next_after

    def search_modified(
        self,
        object_type: str,
        *,
        modified_property: str,
        since: datetime,
        after: str,
        limit: int,
        properties: Sequence[str],
    ) -> HubSpotSearchPage:
        """Return one page of the records modified at or after ``since``, ascending on ``modified_property``.

        ``after`` is the offset into the query's results. ``modified_property`` is
        always read back, so each record states the time it was found by.
        HubSpot's search lags its writes and never returns archived records.
        """

        payload: dict[str, Any] = {
            "filterGroups": [
                {"filters": [{"propertyName": modified_property, "operator": "GTE", "value": str(_epoch_ms(since))}]}
            ],
            "sorts": [{"propertyName": modified_property, "direction": "ASCENDING"}],
            "properties": list(dict.fromkeys((*properties, modified_property))),
            "limit": limit,
        }
        if after:
            payload["after"] = after
        path = f"/crm/objects/{API_VERSION}/{object_type}/search"
        return self._request(HubSpotSearchPage, "POST", path, payload=payload, scopes=(read_scope(object_type),))

    def batch_read(self, object_type: str, ids: Sequence[str], properties: Sequence[str]) -> HubSpotBatchRead:
        """Read ``ids`` in batches of :data:`BATCH_READ_LIMIT`; report the requested ids HubSpot did not return.

        HubSpot leaves out an archived or deleted record. For a merged id it
        returns the surviving record under the survivor's id, so the merged id is
        reported missing too.
        """

        path = f"/crm/objects/{API_VERSION}/{object_type}/batch/read"
        requested = tuple(dict.fromkeys(ids))
        results: list[HubSpotObject] = []
        for chunk in batched(requested, BATCH_READ_LIMIT):
            payload = {
                "inputs": [{"id": key} for key in chunk],
                "properties": list(properties),
                "propertiesWithHistory": [],
            }
            page = self._request(HubSpotBatchRead, "POST", path, payload=payload, scopes=(read_scope(object_type),))
            results.extend(page.results)
        returned = {record.id for record in results}
        return HubSpotBatchRead(results=tuple(results), missing=tuple(key for key in requested if key not in returned))

    def associations(
        self, from_type: str, to_type: str, ids: Sequence[str]
    ) -> dict[str, tuple[HubSpotAssociation, ...]]:
        """Return each of ``ids``'s associated ``to_type`` records; an id without any maps to ``()``.

        Ids go in batches of :data:`ASSOCIATION_BATCH_LIMIT`. A result with more
        associations carries its own paging cursor, which is sent back with its id
        until that record's associations are exhausted.
        """

        path = f"/crm/associations/{API_VERSION}/{from_type}/{to_type}/batch/read"
        scopes = (read_scope(from_type), read_scope(to_type))
        requested = tuple(dict.fromkeys(ids))
        found: dict[str, list[HubSpotAssociation]] = {key: [] for key in requested}
        for chunk in batched(requested, ASSOCIATION_BATCH_LIMIT):
            inputs: list[dict[str, str]] = [{"id": key} for key in chunk]
            while inputs:
                page = self._request(_HubSpotAssociationPage, "POST", path, payload={"inputs": inputs}, scopes=scopes)
                sent = {item["id"]: item.get("after", "") for item in inputs}
                inputs = []
                for result in page.results:
                    key = result.from_.id
                    found.setdefault(key, []).extend(result.to)
                    if result.next_after:
                        if result.next_after == sent.get(key):
                            raise HubSpotError("HubSpot did not advance an association listing.")
                        inputs.append({"id": key, "after": result.next_after})
        return {key: tuple(targets) for key, targets in found.items()}

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


def _retry_policy(response: httpx2.Response, body: _HubSpotErrorBody) -> tuple[bool, timedelta | None]:
    """HubSpot's status table: whether a later attempt can outlive a refusal, and the wait before it.

    423 waits at least two seconds. 429 waits for ``Retry-After`` when HubSpot
    sends it, else for its rate-limit policy's window (ten seconds, or an hour
    for the daily limit). The gateway statuses wait for ``Retry-After`` when sent.
    Every other refusal is permanent.
    """

    status = response.status_code
    if status not in _TRANSIENT_STATUSES:
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


def decode_string(value: str | None) -> str:
    """Return a text property's value, or a single-select enumeration's option value; ``""`` when unset."""

    return (value or "").strip()


def decode_options(value: str | None) -> tuple[str, ...]:
    """Return the option values of a multi-valued enumeration, which HubSpot joins with ``;``."""

    return tuple(part for part in (item.strip() for item in (value or "").split(";")) if part)


def decode_number(value: str | None) -> Decimal | None:
    """Return a number property's value exactly; ``None`` when unset, ``ValueError`` when it is not a number."""

    text = decode_string(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("Not a HubSpot number value.") from error
    if not number.is_finite():
        raise ValueError("Not a HubSpot number value.")
    return number


def decode_bool(value: str | None) -> bool | None:
    """Return a boolean property's value; ``None`` when unset, ``ValueError`` for anything but true or false."""

    text = decode_string(value).lower()
    if not text:
        return None
    if text not in ("true", "false"):
        raise ValueError("Not a HubSpot boolean value.")
    return text == "true"


def decode_datetime(value: str | None) -> datetime | None:
    """Return a ``datetime`` property's instant in UTC, stated in ISO 8601 or epoch milliseconds.

    ``None`` when unset; a time without an offset is UTC; ``ValueError`` when the
    value is neither form.
    """

    text = decode_string(value)
    if not text:
        return None
    if text.removeprefix("-").isdecimal():
        return EPOCH + timedelta(milliseconds=int(text))
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("Not a HubSpot date or time value.") from error
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def decode_date(value: str | None) -> date | None:
    """Return a ``date`` property's day.

    HubSpot states a day as its midnight in UTC, so the day is read in UTC and
    never shifted into a local time zone.
    """

    moment = decode_datetime(value)
    return moment.date() if moment is not None else None
