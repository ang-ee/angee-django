"""Value types for addon resource files."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TypeAlias

import tablib
import yaml
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple

from angee.base.resources.exceptions import ResourceLoadError
from angee.base.resources.fetch import fetch_url

if TYPE_CHECKING:
    from angee.base.apps import BaseAddonConfig


def resolve_model(label: str) -> type[models.Model]:
    """Return the model class named by an ``app_label.ModelName`` label."""

    try:
        app_label, model_name = make_model_tuple(label)
    except ValueError as exc:
        raise ImproperlyConfigured(f"Invalid model label {label!r}") from exc
    if not app_label or not model_name:
        raise ImproperlyConfigured(f"Invalid model label {label!r}")
    try:
        return apps.get_model(app_label, model_name)
    except LookupError as exc:
        raise ImproperlyConfigured(f"Unknown model {label!r}") from exc


ResourceDeclaration: TypeAlias = str | Path | Mapping[str, Any]
"""One declared resource entry: a path string or an options mapping."""

ResourceDeclarations: TypeAlias = (
    ResourceDeclaration | Iterable[ResourceDeclaration] | None
)
"""One declaration or a deterministic iterable of declarations."""

FROZEN_TIERS = frozenset({"install", "demo"})
RESERVED_ROW_KEYS = frozenset({"_xref", "xref", "model", "_meta"})
TEXT_FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}
STRUCTURED_FORMATS = frozenset({"json", "yaml"})


@dataclass(slots=True)
class ResourceEntry:
    """One declared resource file or URL."""

    addon: BaseAddonConfig
    tier: str
    path: str | None = None
    url: str | None = None
    model: str | None = None
    encoding: str = "utf-8"
    depends_on: tuple[str, ...] = ()
    adopt: bool = False
    """Adopt a pre-existing row matched by a unique field when no ledger row
    exists yet, instead of treating the row as new. Off by default."""
    _rows: tuple[ResourceRow, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _local_path: Path | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_declaration(
        cls,
        addon: BaseAddonConfig,
        tier: str,
        entry: Mapping[str, Any],
    ) -> ResourceEntry:
        """Return one entry from a normalized declaration mapping."""

        return cls(
            addon=addon,
            tier=tier,
            path=entry.get("path"),
            url=entry.get("url"),
            model=entry.get("model"),
            encoding=entry.get("encoding") or "utf-8",
            depends_on=tuple(entry.get("depends_on", ())),
            adopt=bool(entry.get("adopt", False)),
        )

    @property
    def source(self) -> str:
        """Return the stable source id: the URL or block-relative path."""

        return self.url or self.path or ""

    @property
    def display(self) -> str:
        """Return a compact owner-qualified source."""

        return f"{self.addon.name}:{self.source}"

    def materialize(self) -> Path:
        """Return a local path for this entry, fetching URLs into the cache."""

        if self._local_path is None:
            if self.url is not None:
                self._local_path = fetch_url(self.url)
            else:
                self._local_path = Path(self.addon.path) / (self.path or "")
        return self._local_path

    def read_resource_rows(self) -> tuple[ResourceRow, ...]:
        """Return parsed rows from this file."""

        if self._rows is None:
            records, file_model = self._read_records()
            self._check_model_conflict(file_model)
            fallback = self.model or file_model
            self._rows = tuple(
                ResourceRow.from_record(
                    self,
                    record,
                    index=index,
                    fallback_model=fallback,
                )
                for index, record in enumerate(records, start=1)
            )
        return self._rows

    def infer_model_label(self) -> str:
        """Infer ``app.Model`` from the tabular filename."""

        stem = PurePosixPath(self.source).name
        suffix = Path(stem).suffix
        if suffix:
            stem = stem[: -len(suffix)]
        prefix, separator, remainder = stem.partition("_")
        if separator and prefix.isdigit():
            stem = remainder
        parts = stem.split(".")
        if len(parts) != 2 or not all(parts):
            raise ImproperlyConfigured(
                f"{self.display} must declare model or use [NNN_]app.model.ext"
            )
        return f"{parts[0]}.{parts[1]}"

    def _read_records(self) -> tuple[list[dict[str, Any]], str | None]:
        """Return parsed records and any file-declared model."""

        path = self.materialize()
        if not path.exists():
            raise ImproperlyConfigured(
                f"{self.addon.name}.resources[{self.tier}] references "
                f"missing file {self.source!r}"
            )
        file_format = self._tablib_format(path)
        if file_format in STRUCTURED_FORMATS:
            return self._read_structured(path, file_format)
        return self._read_tabular(path, file_format), None

    def _read_structured(
        self,
        path: Path,
        file_format: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Read yaml/json, honoring a ``{_meta, rows}`` envelope."""

        text = path.read_text(encoding=self.encoding)
        if not text.strip():
            return [], None
        if file_format == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ImproperlyConfigured(
                    f"{self.display} could not be parsed as json"
                ) from exc
        else:
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ImproperlyConfigured(
                    f"{self.display} could not be parsed as yaml"
                ) from exc
        return self._records_from_object(data)

    def _records_from_object(
        self,
        data: object,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Split a parsed yaml/json document into records and file model."""

        if data is None:
            return [], None
        file_model: str | None = None
        if isinstance(data, Mapping):
            meta = data.get("_meta")
            if isinstance(meta, Mapping) and isinstance(
                meta.get("model"), str
            ):
                file_model = meta["model"]
            elif isinstance(data.get("model"), str):
                file_model = data["model"]
            raw_rows: object = data.get("rows")
            if raw_rows is None:
                raise ImproperlyConfigured(
                    f"{self.display}: mapping form must contain `rows`"
                )
        else:
            raw_rows = data
        if not isinstance(raw_rows, list):
            raise ImproperlyConfigured(
                f"{self.display}: resource data must be a list of rows"
            )
        records: list[dict[str, Any]] = []
        for index, row in enumerate(raw_rows, start=1):
            if not isinstance(row, Mapping):
                raise ImproperlyConfigured(
                    f"{self.display} row {index}: row must be a mapping"
                )
            records.append(dict(row))
        return records, file_model

    def _read_tabular(
        self,
        path: Path,
        file_format: str,
    ) -> list[dict[str, Any]]:
        """Read csv/tsv records through tablib."""

        content = path.read_text(encoding=self.encoding)
        if not content.strip():
            return []
        dataset = tablib.Dataset()
        try:
            dataset.load(content, format=file_format)
        except Exception as exc:
            raise ImproperlyConfigured(
                f"{self.display} could not be parsed as {file_format}"
            ) from exc
        return [dict(row) for row in dataset.dict]

    def _check_model_conflict(self, file_model: str | None) -> None:
        """Fail when an explicit entry model disagrees with the file model."""

        if (
            self.model
            and file_model
            and _normalize_label(self.model) != _normalize_label(file_model)
        ):
            raise ResourceLoadError(
                f"{self.display}: model conflict; entry declares "
                f"{self.model!r}, file declares {file_model!r}"
            )

    def _tablib_format(self, path: Path) -> str:
        """Return the tablib format for this entry's local file."""

        suffix = path.suffix.lower()
        file_format = TEXT_FORMATS.get(suffix)
        if file_format is None:
            allowed = ", ".join(sorted(TEXT_FORMATS))
            raise ImproperlyConfigured(
                f"{self.display} has unsupported format {suffix!r}; "
                f"expected one of {allowed}"
            )
        return file_format


@dataclass(slots=True)
class ResourceRow:
    """One normalized resource row."""

    entry: ResourceEntry
    model_label: str
    xref: str
    values: dict[str, Any]

    @classmethod
    def from_record(
        cls,
        entry: ResourceEntry,
        record: Mapping[str, Any],
        *,
        index: int,
        fallback_model: str | None = None,
    ) -> ResourceRow:
        """Return one normalized row from a parsed record."""

        payload = dict(record)
        raw_model = (
            payload.get("model") or fallback_model or entry.infer_model_label()
        )
        raw_xref = payload.get("_xref") or payload.get("xref")
        if not isinstance(raw_xref, str) or not raw_xref.strip():
            raise ResourceLoadError(
                f"{entry.display} row {index}: missing _xref"
            )
        values = cls._values_for(payload)
        return cls(
            entry=entry,
            model_label=str(raw_model),
            xref=raw_xref.strip(),
            values=values,
        )

    @property
    def dataset_row(self) -> dict[str, Any]:
        """Return this row as a ModelResource dataset mapping."""

        return {"_xref": self.xref, **self.values}

    @staticmethod
    def _values_for(payload: dict[str, Any]) -> dict[str, Any]:
        """Return model field values from a row envelope."""

        if "fields" in payload:
            fields_value = payload["fields"]
            if not isinstance(fields_value, Mapping):
                raise ImproperlyConfigured(
                    "resource row fields must map names"
                )
            return dict(fields_value)
        return {
            key: value
            for key, value in payload.items()
            if key not in RESERVED_ROW_KEYS
        }


@dataclass(slots=True)
class ResourceGroup:
    """Rows from one entry targeting one model."""

    entry: ResourceEntry
    model: type[models.Model]
    rows: list[ResourceRow]

    def to_dataset(self) -> tablib.Dataset:
        """Return a tablib dataset for import-export."""

        headers = self._headers()
        dataset = tablib.Dataset(headers=headers)
        for row in self.rows:
            values = row.dataset_row
            dataset.append([values.get(header) for header in headers])
        return dataset

    def _headers(self) -> list[str]:
        """Return stable headers preserving source row order."""

        seen = {"_xref"}
        headers = ["_xref"]
        for row in self.rows:
            for key in row.values:
                if key in seen:
                    continue
                seen.add(key)
                headers.append(key)
        return headers


@dataclass(slots=True)
class ValidationResult:
    """Counts returned by resource validation."""

    checked_files: int
    checked_rows: int


@dataclass(slots=True)
class LoadResult:
    """Counts returned by a resource load."""

    created: int
    updated: int
    skipped: int

    @property
    def loaded(self) -> int:
        """Total rows created or updated."""

        return self.created + self.updated


def _normalize_label(label: str) -> str:
    """Return a case-insensitive ``app.model`` key for conflict checks."""

    return label.replace(" ", "").lower()
