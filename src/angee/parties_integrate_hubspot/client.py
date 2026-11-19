"""HubSpot's CRM REST API at the date-versioned ``2026-09`` paths, over integrate's HTTP owner.

The one owner of the HubSpot wire protocol for this addon. Every request is
integrate's pinned, byte-capped request (``HttpClient.request``) with the
authorization headers the caller supplies; the client never reads a credential.
Every answer is validated into a pydantic model that ignores the fields it does
not name, so a new HubSpot field never breaks a sync and a missing required one
is a :class:`HubSpotError`; its records are validated one by one, so one bad
record is that record's alone. A page also states HubSpot's time from the answer's
``Date`` header (:attr:`HubSpotPage.sent_at`), which is why the client reads the
response itself rather than through ``HttpClient.request_model``. No HubSpot
client library is involved.

Integrate's ``HttpRefusal`` classifies whatever brings a request no usable
answer, and :meth:`HubSpotError.of` turns it into HubSpot's error, adding only
what is HubSpot's own (its docstring lists it) and never HubSpot's message text.

HubSpot states every property value as a nullable string; the ``parse_*``
functions turn one into a Python value by property type (text and single
choice, multiple choice, number, bool, datetime, date). They are public for the
consumers' directory mappings, whose author knows the type of each property
mapped. Which property states a record's last-modified time depends on its
object type, and the client alone knows it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from itertools import batched
from typing import Any, Self
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic.alias_generators import to_camel

from angee.integrate.errors import IntegrationError
from angee.integrate.http import JSON_MAX_BYTES, HttpClient, HttpRefusal

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

MISSING_SCOPES = "MISSING_SCOPES"
"""The ``category`` of HubSpot's refusal of a request whose scope the key lacks."""

SERVICE_KEY_SHAPE = re.compile(r"pat-[a-z0-9]+-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
"""The usual shape of a HubSpot service key: ``pat-``, a region, and a UUID.

HubSpot publishes no key format, so the shape never refuses a key; it only adds
a hint to HubSpot's rejection of one that lacks it (:meth:`HubSpotError.of`).
"""

MERGED_PROPERTY = "hs_merged_object_ids"
"""The property in which a merge's surviving record names the records it replaced, joined with ``;``."""

_MODIFIED_PROPERTY = {"contacts": "lastmodifieddate"}
_RECORD_ID = "hs_object_id"
_NOT_FOUND = "OBJECT_NOT_FOUND"
_REQUEST_TIMEOUT_SECONDS = 30
_LOCKED_WAIT = timedelta(seconds=2)
_RATE_LIMIT_WAITS = {"TEN_SECONDLY_ROLLING": timedelta(seconds=10), "DAILY": timedelta(hours=1)}
_HOST_NAME = re.compile(r"[A-Za-z0-9.-]+")


def get_read_scope(object_type: str) -> str:
    """Return the scope a service key needs to read records of a standard ``object_type``."""

    return f"crm.objects.{object_type}.read"


class HubSpotError(IntegrationError):
    """A HubSpot refusal whose message is composed from facts, never from HubSpot's text.

    ``status`` is the HTTP status of the refused answer, or ``None`` when the
    failure is not a refused answer (unreachable, oversized, unreadable, or a
    record HubSpot could not read). ``category`` is the one HubSpot stated for a
    refused answer (:data:`MISSING_SCOPES`, say), else ``None``.
    """

    status: int | None = None
    category: str | None = None

    @classmethod
    def of(cls, refusal: HttpRefusal, *, scopes: Sequence[str] = (), usual_key: bool = True) -> HubSpotError:
        """Return HubSpot's error for ``refusal``; ``scopes`` are what the request needed.

        HubSpot's own cases: a 423 (records locked for a moment) is transient and
        waits at least two seconds; a 429 without ``Retry-After`` waits for the
        window of the rate-limit policy its body names (ten seconds, or an hour for
        the daily limit), and one under the daily limit says so; a rejected key, and
        a missing scope naming ``scopes``, are worded for the operator. A rejected
        key that is not ``usual_key`` (:data:`SERVICE_KEY_SHAPE`) adds a hint to
        check it for extra or missing characters. ``HttpRefusal`` decides and words
        the rest.
        """

        try:
            facts = _HubSpotErrorBody.model_validate(refusal.body or {})
        except ValidationError:
            facts = _HubSpotErrorBody()
        if refusal.status == 423:
            wait = max(refusal.retry_after or _LOCKED_WAIT, _LOCKED_WAIT)
            error = cls("HubSpot has locked these records for a moment.", transient=True, retry_after=wait)
        else:
            wait = None
            if refusal.status == 429 and refusal.retry_after is None:
                wait = _RATE_LIMIT_WAITS.get(facts.policy_name or "", _RATE_LIMIT_WAITS["TEN_SECONDLY_ROLLING"])
            message = None
            if refusal.status == 401:
                message = "HubSpot rejected the access token. Enter a valid HubSpot service key."
                if not usual_key:
                    message += " This key lacks a service key's usual shape: check it for extra or missing characters."
            elif refusal.status == 403 and facts.category == MISSING_SCOPES:
                needed = ", ".join(scopes) or "the scope this request needs"
                message = f"The HubSpot service key is missing a scope this connection needs: {needed}."
            elif refusal.status == 429 and facts.policy_name == "DAILY":
                message = "This HubSpot account has used its daily API request limit."
            error = refusal.as_error(cls, "HubSpot", public_message=message, retry_after=wait)
        error.status, error.category = refusal.status, facts.category
        return error


class _HubSpotModel(BaseModel):
    """HubSpot's camelCase JSON read into snake_case fields; unknown fields are ignored."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_alias=True, validate_by_name=True, extra="ignore", frozen=True
    )


class _HubSpotErrorBody(_HubSpotModel):
    """The facts of a HubSpot error answer a refusal is composed from; a body of another shape states none."""

    category: str | None = None
    policy_name: str | None = None


class HubSpotTokenInfo(_HubSpotModel):
    """What HubSpot's token introspection states about one access token: the account (hub) it belongs to.

    The answer also lists scopes, which this client never reads: HubSpot need not
    list every scope a key holds there.
    """

    hub_id: int


class HubSpotAccount(_HubSpotModel):
    """The account (portal, or hub) a token belongs to, and the host its web app serves it from.

    ``ui_domain`` is ``None`` when the answer states no host name: it only names
    source links, so an absent or unexpected one never refuses the account.
    """

    portal_id: int
    ui_domain: str | None = None

    @field_validator("ui_domain", mode="before")
    @classmethod
    def _host_name_or_none(cls, value: Any) -> str | None:
        return value if isinstance(value, str) and _HOST_NAME.fullmatch(value) else None


class _HubSpotKey(_HubSpotModel):
    """A record's id as HubSpot states it: a string of digits."""

    id: str = Field(pattern=r"^[0-9]+$")


class HubSpotObject(_HubSpotKey):
    """One CRM record; HubSpot states every property value as a nullable string.

    ``unreadable`` marks a record HubSpot stated in another shape (a property value
    that is not a string, say): only its id was read, and ``properties`` is empty.
    """

    properties: dict[str, str | None] = Field(default_factory=dict)
    unreadable: bool = False

    def modified_at(self, object_type: str) -> datetime | None:
        """Return the record's last-modified time, or ``None`` when it was not read or cannot be parsed."""

        try:
            return parse_datetime(self.properties.get(_MODIFIED_PROPERTY[object_type]))
        except ValueError:
            return None

    def merged_ids(self) -> tuple[str, ...]:
        """Return the ids of the records a merge folded into this one, in HubSpot's order (:data:`MERGED_PROPERTY`)."""

        return parse_options(self.properties.get(MERGED_PROPERTY))


class HubSpotNext(_HubSpotModel):
    """The position a following page starts from."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    after: str


class HubSpotPaging(_HubSpotModel):
    """A paged answer's link to its following page, absent on the last one."""

    next: HubSpotNext | None = None


class _HubSpotRecords(_HubSpotModel):
    """An answer's records, each validated on its own, so one bad record never fails the rest.

    A record that does not validate is kept by its id as an ``unreadable`` one; one
    without a usable id is only counted in ``unkeyed``.
    """

    results: tuple[HubSpotObject, ...] = ()
    unkeyed: int = 0

    @model_validator(mode="before")
    @classmethod
    def _records_one_by_one(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not isinstance(stated := data.get("results"), list):
            return data
        results: list[HubSpotObject] = []
        unkeyed = 0
        for item in stated:
            try:
                results.append(HubSpotObject.model_validate(item))
            except ValidationError:
                try:
                    results.append(HubSpotObject(id=_HubSpotKey.model_validate(item).id, unreadable=True))
                except ValidationError:
                    unkeyed += 1
        return {**data, "results": results, "unkeyed": unkeyed}


class HubSpotPage(_HubSpotRecords):
    """One page of a list walk or a search, and HubSpot's time when it answered.

    A list page's following position is the id of the record it starts at; a
    search page's is an offset into the query's results, which this client reads
    only as a sign that more results follow. A page with no records that names a
    following one is refused: no walk, search or drain could move past it.
    ``total`` is the number of records a search counts for its query, ``None`` on
    a list page. ``sent_at`` is the time the answer's ``Date`` header states,
    HubSpot's clock to the second; ``None`` when it states none that can be read.
    """

    paging: HubSpotPaging | None = None
    total: int | None = None
    sent_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _sent_at(cls, data: Any, info: ValidationInfo) -> Any:
        headers = (info.context or {}).get("headers")
        if headers is None or not isinstance(data, dict):
            return data
        try:
            stated = parsedate_to_datetime(headers.get("date", ""))
        except (TypeError, ValueError, IndexError):
            stated = None
        return {**data, "sent_at": stated if stated is not None and stated.tzinfo is not None else None}

    @model_validator(mode="after")
    def _records_before_more(self) -> Self:
        if self.next_after and not self.results:
            raise ValueError("A page without records names a following one.")
        return self

    @property
    def next_after(self) -> str:
        """Return the following page's position, or ``""`` on the last page."""

        return self.paging.next.after if self.paging is not None and self.paging.next is not None else ""


class _HubSpotErrorContext(_HubSpotModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    ids: tuple[str, ...] = ()


class _HubSpotBatchError(_HubSpotModel):
    """One error a partial (207) batch answer states for some of its records, naming them in its context."""

    category: str | None = None
    context: _HubSpotErrorContext | None = None

    @property
    def ids(self) -> tuple[str, ...]:
        """Return the record ids the error names."""

        return self.context.ids if self.context is not None else ()


class _HubSpotBatchAnswer(_HubSpotRecords):
    errors: tuple[_HubSpotBatchError, ...] = ()


@dataclass(frozen=True, slots=True)
class HubSpotBatchRead:
    """The records a batch read found, and the requested ids HubSpot says it has no record under.

    A found record may carry another id than the one requested: a merge replaces
    the merged ids with its surviving record's, so a merged id is missing beside
    the survivor that names it.
    """

    found: tuple[HubSpotObject, ...]
    missing: tuple[str, ...]


class HubSpotClient:
    """One HubSpot account's CRM, read with the authorization headers its caller supplies.

    The caller owns the credential: it passes the credential's own authorization
    headers, and the key itself to :meth:`token_info`, the one request that carries
    it in a body. Each method sends only the requests its docstring names; nothing
    is cached between calls.
    """

    def __init__(self, headers: Mapping[str, str], *, usual_key: bool = True) -> None:
        """Bind the authorization headers every request carries.

        ``usual_key`` states whether the caller's key has a service key's usual
        shape (:data:`SERVICE_KEY_SHAPE`); a rejection of one that does not says to
        check it (:meth:`HubSpotError.of`).
        """

        self.headers = dict(headers)
        self.usual_key = usual_key
        self.http = HttpClient()

    def token_info(self, token_key: str) -> HubSpotTokenInfo:
        """Introspect ``token_key``: the hub it belongs to.

        HubSpot documents introspection for private-app keys only, and the key
        travels in the request body, so a caller asks only when the account call
        cannot answer.
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
        record it starts at. The walk starts at the id after ``after``, so it seeks
        past ``after`` whether or not that record still exists, without reading the
        records before it, and each page of ids is one request; an id at or below
        ``after`` is still dropped. Each page is requested as the caller reaches it.
        """

        floor = int(after) if after else None
        position = "" if floor is None else str(floor + 1)
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
        """Read ``ids`` in batches of :data:`BATCH_READ_LIMIT`; ``properties`` come back with :data:`MERGED_PROPERTY`.

        Only HubSpot's word makes an id missing: a not-found error that names it,
        or a found record that names it among the records its merge replaced. Any
        other id HubSpot leaves unanswered, and any other error it states for a
        record, is a transient :class:`HubSpotError`, so a record HubSpot did not
        read is never reported missing.
        """

        path = f"/crm/objects/{API_VERSION}/{object_type}/batch/read"
        requested = tuple(dict.fromkeys(ids))
        found: list[HubSpotObject] = []
        gone: set[str] = set()
        for chunk in batched(requested, BATCH_READ_LIMIT):
            payload = {
                "inputs": [{"id": key} for key in chunk],
                "properties": list(dict.fromkeys((*properties, MERGED_PROPERTY))),
                "propertiesWithHistory": [],
            }
            answer = self._request(
                _HubSpotBatchAnswer, "POST", path, payload=payload, scopes=(get_read_scope(object_type),)
            )
            if failed := next((error for error in answer.errors if error.category != _NOT_FOUND), None):
                stated = f" ({failed.category})" if failed.category else ""
                raise HubSpotError(f"HubSpot could not read some records{stated}.", transient=True)
            found.extend(answer.results)
            gone.update(key for error in answer.errors for key in error.ids)
        answered = {record.id for record in found}
        gone.update(key for record in found for key in record.merged_ids())
        if any(key not in answered and key not in gone for key in requested):
            raise HubSpotError("HubSpot did not answer for some records.", transient=True)
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
        """Send one JSON request with ``payload`` as its body and validate the answer into ``model``.

        Integrate's request refuses a transport failure, an oversized answer and
        one it cannot decode, ``HttpRefusal.of`` a non-success, and an answer that
        does not validate is unreadable: every refusal is a :class:`HubSpotError`.
        The answer's headers are the validation context, so a page states its
        ``Date``.
        """

        def error(refusal: HttpRefusal) -> HubSpotError:
            return HubSpotError.of(refusal, scopes=scopes, usual_key=self.usual_key)

        response = self.http.request(
            method,
            f"{HUBSPOT_API}{path}" + (f"?{urlencode(query)}" if query else ""),
            headers={"Accept": "application/json", **self.headers},
            json=payload,
            max_bytes=JSON_MAX_BYTES,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            error=error,
        )
        if not response.is_success:
            raise error(HttpRefusal.of(response))
        try:
            return model.model_validate_json(response.content, context={"headers": response.headers})
        except ValueError as failure:  # pydantic's ValidationError, or JSON that does not decode
            raise error(HttpRefusal(failure=failure)) from failure


def _requested(object_type: str, properties: Sequence[str]) -> list[str]:
    """Return ``properties`` with ``object_type``'s modified property, which every read needs."""

    return list(dict.fromkeys((*properties, _MODIFIED_PROPERTY[object_type])))


def _epoch_ms(moment: datetime) -> int:
    """Return ``moment`` as HubSpot's epoch milliseconds; a naive time is UTC."""

    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return (aware - EPOCH) // timedelta(milliseconds=1)


def parse_string(value: str | None) -> str:
    """Return a text property's value, or a single-select enumeration's option value; ``""`` when unset."""

    return (value or "").strip()


def parse_options(value: str | None) -> tuple[str, ...]:
    """Return a multiple-choice (checkbox) property's option values, which HubSpot joins with ``;``."""

    return tuple(part for part in (item.strip() for item in (value or "").split(";")) if part)


def parse_number(value: str | None) -> Decimal | None:
    """Return a ``number`` property's value exactly; ``None`` when unset, ``ValueError`` when it is not a number."""

    text = parse_string(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("Not a HubSpot number value.") from error
    if not number.is_finite():
        raise ValueError("Not a HubSpot number value.")
    return number


def parse_bool(value: str | None) -> bool | None:
    """Return a ``bool`` property's value; ``None`` when unset, ``ValueError`` for anything but true or false."""

    text = parse_string(value).lower()
    if not text:
        return None
    if text not in ("true", "false"):
        raise ValueError("Not a HubSpot boolean value.")
    return text == "true"


def parse_datetime(value: str | None) -> datetime | None:
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


def parse_date(value: str | None) -> date | None:
    """Return a ``date`` property's day; ``None`` when unset, ``ValueError`` when it is not a day.

    HubSpot states a day as its midnight in UTC, so the day is read in UTC and
    never shifted into a local time zone.
    """

    moment = parse_datetime(value)
    return moment.date() if moment is not None else None
