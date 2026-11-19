"""HubSpot's CRM REST API at the date-versioned ``2026-09`` paths, over integrate's HTTP owner.

The one owner of the HubSpot wire protocol for this addon. Every request is
integrate's typed request (``HttpClient.request_model``: SSRF-pinned,
byte-capped) with the authorization headers the caller supplies; the client never
reads a credential. Every answer is validated into a pydantic model that ignores
the fields it does not name, so a new HubSpot field never breaks a sync and a
missing required one is a :class:`HubSpotError`. No HubSpot client library is
involved.

Integrate's ``HttpRefusal`` classifies whatever brings a request no usable
answer, and :meth:`HubSpotError.of` turns it into HubSpot's error, adding only
what is HubSpot's own (its docstring lists it) and never HubSpot's message text.

HubSpot states every property value as a nullable string; the ``parse_*``
functions turn one into a Python value by property type. Which property states
a record's last-modified time depends on its object type, and the client alone
knows it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Any, Self
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.alias_generators import to_camel

from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClient, HttpRefusal

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
    only as a sign that more results follow. A page with no records that names a
    following one is refused: no walk, search or drain could move past it.
    """

    results: tuple[HubSpotObject, ...] = ()
    paging: HubSpotPaging | None = None

    @model_validator(mode="after")
    def _records_before_more(self) -> Self:
        if self.next_after and not self.results:
            raise ValueError("A page without records names a following one.")
        return self

    @property
    def next_after(self) -> str:
        """Return the following page's position, or ``""`` on the last page."""

        return self.paging.next.after if self.paging is not None and self.paging.next is not None else ""


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
        """Send one typed request with ``payload`` as its JSON body; any refusal is a :class:`HubSpotError`."""

        return self.http.request_model(
            method,
            f"{HUBSPOT_API}{path}" + (f"?{urlencode(query)}" if query else ""),
            headers=self.headers,
            json=payload,
            model=model,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            error=lambda refusal: HubSpotError.of(refusal, scopes=scopes, usual_key=self.usual_key),
        )


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
