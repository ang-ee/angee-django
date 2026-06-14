"""Value objects for declared addon resource files and parsed rows."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypeAlias

import tablib
import yaml
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.db import models
from django.db.models.utils import make_model_tuple
from django.utils._os import safe_join
from import_export.results import RowResult

from angee.resources.exceptions import ResourceLoadError
from angee.resources.fetch import fetch_url
from angee.resources.tiers import ResourceTier


class _ResourceAddon(Protocol):
    """Addon facts consumed by resource declarations."""

    @property
    def name(self) -> str:
        """Return the full dotted Django app name."""

    @property
    def label(self) -> str:
        """Return the short Django app label."""

    @property
    def path(self) -> str:
        """Return the filesystem path to the addon package root."""


def resolve_model(label: str) -> type[models.Model]:
    """Return the model class named by an ``app_label.ModelName`` label."""

    try:
        app_label, model_name = make_model_tuple(label)
    except ValueError as error:
        raise ImproperlyConfigured(f"Invalid model label {label!r}") from error
    if not app_label or not model_name:
        raise ImproperlyConfigured(f"Invalid model label {label!r}")
    try:
        return apps.get_model(app_label, model_name)
    except LookupError as error:
        raise ImproperlyConfigured(f"Unknown model {label!r}") from error


ResourceDeclaration: TypeAlias = Mapping[str, Any]
"""One normalized resource entry declaration."""

AdoptDeclaration: TypeAlias = str | bool | tuple[str, ...]
"""Normalized resource adoption declaration."""

EntryKey = tuple[str, str]
"""Dependency graph key identifying one resource entry by addon and source."""

RESERVED_ROW_KEYS = frozenset({"_xref", "xref", "model", "_meta"})
"""Row keys interpreted by the resource loader rather than model fields."""

TEXT_FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}
"""Supported text resource formats keyed by file suffix."""

STRUCTURED_FORMATS = frozenset({"json", "yaml"})
"""Text formats that can carry ``_meta`` and ``rows`` envelopes."""

FROZEN_TIERS = frozenset(ResourceTier.from_value(tier) for tier in (ResourceTier.INSTALL, ResourceTier.DEMO))
"""Tiers whose loaded rows are not updated after ledger creation."""


def resource_manifest_for(app_config: AppConfig) -> dict[str, tuple[dict[str, Any], ...]]:
    """Return normalized resource declarations keyed by tier for one addon."""

    manifest: dict[str, tuple[dict[str, Any], ...]] = {tier: () for tier in ResourceTier.values}
    for raw_tier, declarations in (getattr(app_config, "resources", {}) or {}).items():
        tier = _resource_tier_value(raw_tier)
        manifest[tier] = _resource_entries(app_config, declarations)
    return manifest


def _resource_entries(
    app_config: AppConfig,
    declarations: object,
) -> tuple[dict[str, Any], ...]:
    """Return normalized entry dictionaries for one resource tier."""

    if declarations is None:
        return ()
    if isinstance(declarations, str | Path | Mapping):
        return (_resource_entry(app_config, declarations),)
    if not isinstance(declarations, Iterable):
        raise ImproperlyConfigured(f"{declarations!r} is not a resource entry or iterable")
    return tuple(_resource_entry(app_config, entry) for entry in declarations)


def _resource_entry(app_config: AppConfig, declaration: object) -> dict[str, Any]:
    """Return one normalized resource entry dictionary."""

    if isinstance(declaration, str | Path):
        return {"path": _relative_app_path(app_config, declaration)}
    if not isinstance(declaration, Mapping):
        raise ImproperlyConfigured(f"{declaration!r} is not a resource path or mapping")

    entry: dict[str, Any] = {str(key): declaration[key] for key in declaration if key not in {"path", "url"}}
    path = declaration.get("path")
    url = declaration.get("url")
    if (path is None) == (url is None):
        raise ImproperlyConfigured(f"resource entry {dict(declaration)!r} must set exactly one of 'path' or 'url'")
    if path is None:
        entry["url"] = str(url)
    else:
        entry["path"] = _relative_app_path(app_config, path)
    if "depends_on" in entry:
        entry["depends_on"] = _normalize_depends_on(entry["depends_on"])
    if "adopt" in entry:
        entry["adopt"] = _resource_adopt_value(entry["adopt"])
    if "model" in entry and entry["model"] is not None:
        entry["model"] = str(entry["model"])
    if "encoding" in entry:
        entry["encoding"] = str(entry["encoding"] or "utf-8")
    return entry


def _resource_adopt_value(value: object) -> AdoptDeclaration:
    """Return the normalized adoption declaration for one resource entry."""

    if isinstance(value, str):
        return value
    if isinstance(value, Iterable) and not isinstance(value, bytes | Mapping):
        fields = tuple(str(item) for item in value)
        if not fields:
            raise ImproperlyConfigured("adopt must name at least one field")
        return fields
    return bool(value)


def _relative_app_path(app_config: AppConfig, value: object) -> str:
    """Return one safe path relative to ``app_config.path``."""

    raw = str(value)
    if not raw:
        raise ImproperlyConfigured("Manifest path must not be empty")
    try:
        safe_join(app_config.path, raw)
    except SuspiciousFileOperation as error:
        raise ImproperlyConfigured(f"Manifest path {raw!r} must be relative and stay inside the addon") from error
    return raw


def _resource_tier_value(value: object) -> str:
    """Return one normalized resource tier value."""

    raw = str(getattr(value, "value", value))
    if raw not in ResourceTier.values:
        expected = ", ".join(ResourceTier.values)
        raise ImproperlyConfigured(f"Unknown resource tier {raw!r}; expected one of {expected}")
    return raw


def _normalize_depends_on(value: object) -> tuple[str, ...]:
    """Return dependency keys, treating a bare string as a single key."""

    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Iterable):
        raise ImproperlyConfigured("depends_on must be a string or iterable of strings")
    return tuple(str(item) for item in value)


@dataclass(slots=True)
class ResourceEntry:
    """One local or remote resource file declared by an addon."""

    addon: _ResourceAddon
    """Addon that owns this resource declaration."""

    tier: str
    """Normalized resource tier value."""

    path: str | None = None
    """Addon-relative local file path, when the source is local."""

    url: str | None = None
    """Remote file URL, when the source is fetched into the cache."""

    model: str | None = None
    """Optional fallback model label for every row in the file."""

    encoding: str = "utf-8"
    """Text encoding used when reading the materialized file."""

    depends_on: tuple[str, ...] = ()
    """Resource source keys that must load before this entry."""

    adopt: AdoptDeclaration = False
    """Unique field(s) used for adoption; ``True`` infers one unique field."""

    _rows: tuple[ResourceRow, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _local_path: Path | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_declaration(
        cls,
        addon: _ResourceAddon,
        tier: str,
        declaration: ResourceDeclaration,
    ) -> ResourceEntry:
        """Return an entry from a normalized addon resource declaration."""

        return cls(
            addon=addon,
            tier=tier,
            path=declaration.get("path"),
            url=declaration.get("url"),
            model=declaration.get("model"),
            encoding=declaration.get("encoding", "utf-8"),
            depends_on=declaration.get("depends_on", ()),
            adopt=_resource_adopt_value(declaration["adopt"]) if "adopt" in declaration else False,
        )

    @property
    def source(self) -> str:
        """Return this entry's stable URL or addon-relative path."""

        return self.url or self.path or ""

    @property
    def display(self) -> str:
        """Return an owner-qualified name for diagnostics."""

        return f"{self.addon.name}:{self.source}"

    def materialize(self) -> Path:
        """Return the local file path, fetching remote sources once."""

        if self._local_path is not None:
            return self._local_path
        if self.url is not None:
            self._local_path = fetch_url(self.url)
        else:
            self._local_path = Path(self.addon.path) / (self.path or "")
        return self._local_path

    def read_resource_rows(self) -> tuple[ResourceRow, ...]:
        """Return parsed and normalized rows from this entry."""

        if self._rows is None:
            records, file_model = self._read_records()
            self._check_model_conflict(file_model)
            fallback_model = self.model or file_model
            self._rows = tuple(
                ResourceRow.from_record(
                    self,
                    record,
                    index=index,
                    fallback_model=fallback_model,
                )
                for index, record in enumerate(records, start=1)
            )
        return self._rows

    def infer_model_label(self) -> str:
        """Infer ``app.Model`` from a ``[NNN_]app.model.ext`` filename."""

        stem = PurePosixPath(self.source).name
        suffix = Path(stem).suffix
        if suffix:
            stem = stem[: -len(suffix)]
        prefix, separator, remainder = stem.partition("_")
        if separator and prefix.isdigit():
            stem = remainder
        parts = stem.split(".")
        if len(parts) != 2 or not all(parts):
            raise ImproperlyConfigured(f"{self.display} must declare model or use [NNN_]app.model.ext")
        return f"{parts[0]}.{parts[1]}"

    def _read_records(self) -> tuple[list[dict[str, Any]], str | None]:
        """Return parsed row mappings and any file-declared model."""

        path = self.materialize()
        if not path.exists():
            raise ImproperlyConfigured(
                f"{self.addon.name}.resources[{self.tier}] references missing file {self.source!r}"
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
        """Read a JSON or YAML file into row mappings and file metadata."""

        text = path.read_text(encoding=self.encoding)
        if not text.strip():
            return [], None
        if file_format == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as error:
                raise ImproperlyConfigured(f"{self.display} could not be parsed as json") from error
        else:
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as error:
                raise ImproperlyConfigured(f"{self.display} could not be parsed as yaml") from error
        return self._records_from_object(data)

    def _records_from_object(self, data: object) -> tuple[list[dict[str, Any]], str | None]:
        """Return row mappings and an optional file model from parsed data."""

        if data is None:
            return [], None
        file_model: str | None = None
        if isinstance(data, Mapping):
            meta = data.get("_meta")
            if isinstance(meta, Mapping) and isinstance(meta.get("model"), str):
                file_model = meta["model"]
            elif isinstance(data.get("model"), str):
                file_model = data["model"]
            rows = data.get("rows")
            if rows is None:
                raise ImproperlyConfigured(f"{self.display}: mapping form must contain `rows`")
        else:
            rows = data
        if not isinstance(rows, list):
            raise ImproperlyConfigured(f"{self.display}: resource data must be a list of rows")

        records: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                raise ImproperlyConfigured(f"{self.display} row {index}: row must be a mapping")
            records.append(dict(row))
        return records, file_model

    def _read_tabular(
        self,
        path: Path,
        file_format: str,
    ) -> list[dict[str, Any]]:
        """Read CSV or TSV rows with tablib."""

        content = path.read_text(encoding=self.encoding)
        if not content.strip():
            return []
        dataset = tablib.Dataset()
        try:
            dataset.load(content, format=file_format)
        except Exception as error:
            raise ImproperlyConfigured(f"{self.display} could not be parsed as {file_format}") from error
        return [dict(row) for row in dataset.dict]

    def _check_model_conflict(self, file_model: str | None) -> None:
        """Raise when entry and file metadata declare different models."""

        if self.model and file_model and self._model_key(self.model) != self._model_key(file_model):
            raise ResourceLoadError(
                f"{self.display}: model conflict; entry declares {self.model!r}, file declares {file_model!r}"
            )

    def _model_key(self, label: str) -> tuple[str, str]:
        """Return Django's identity tuple for one declared model label."""

        try:
            app_label, model_name = make_model_tuple(label)
        except ValueError as error:
            raise ImproperlyConfigured(f"Invalid model label {label!r}") from error
        return app_label.lower(), model_name

    def _tablib_format(self, path: Path) -> str:
        """Return the tablib format name for ``path``."""

        suffix = path.suffix.lower()
        file_format = TEXT_FORMATS.get(suffix)
        if file_format is None:
            expected = ", ".join(sorted(TEXT_FORMATS))
            raise ImproperlyConfigured(f"{self.display} has unsupported format {suffix!r}; expected one of {expected}")
        return file_format


@dataclass(frozen=True, slots=True)
class EntryGraph:
    """Dependency graph for a selected set of resource entries."""

    entries: tuple[ResourceEntry, ...]
    """Entries selected for one resource operation."""

    @classmethod
    def from_entries(cls, entries: Iterable[ResourceEntry]) -> EntryGraph:
        """Return a graph over ``entries`` in their selected order."""

        return cls(tuple(entries))

    def ordered(self) -> tuple[ResourceEntry, ...]:
        """Return entries in dependency order, preserving independent order."""

        by_key, position, addon_names = self._index_entries()
        outgoing: dict[EntryKey, list[EntryKey]] = defaultdict(list)
        indegree = {key: 0 for key in by_key}
        for key, entry in by_key.items():
            for dependency in entry.depends_on:
                dependency_key = self._dependency_key(entry, dependency, addon_names)
                if dependency_key not in by_key:
                    raise ResourceLoadError(f"{entry.display}: depends_on target not selected: {dependency}")
                outgoing[dependency_key].append(key)
                indegree[key] += 1
        return self._topological_order(by_key, position, outgoing, indegree)

    def _index_entries(
        self,
    ) -> tuple[dict[EntryKey, ResourceEntry], dict[EntryKey, int], dict[str, str]]:
        """Return graph indexes keyed by addon and source."""

        by_key: dict[EntryKey, ResourceEntry] = {}
        position: dict[EntryKey, int] = {}
        addon_names: dict[str, str] = {}
        for index, entry in enumerate(self.entries):
            key = self._entry_key(entry)
            if key in by_key:
                raise ResourceLoadError(f"duplicate resource entry {entry.display}")
            by_key[key] = entry
            position[key] = index
            addon_names[entry.addon.name] = entry.addon.name
            addon_names[entry.addon.label] = entry.addon.name
        return by_key, position, addon_names

    def _topological_order(
        self,
        by_key: dict[EntryKey, ResourceEntry],
        position: dict[EntryKey, int],
        outgoing: dict[EntryKey, list[EntryKey]],
        indegree: dict[EntryKey, int],
    ) -> tuple[ResourceEntry, ...]:
        """Return the dependency topological order for the indexed graph."""

        ready = sorted(
            (key for key, count in indegree.items() if count == 0),
            key=position.__getitem__,
        )
        ordered: list[ResourceEntry] = []
        while ready:
            key = ready.pop(0)
            ordered.append(by_key[key])
            for child in sorted(outgoing[key], key=position.__getitem__):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            ready.sort(key=position.__getitem__)
        if len(ordered) != len(by_key):
            raise ResourceLoadError("cycle detected in resource depends_on")
        return tuple(ordered)

    def _entry_key(self, entry: ResourceEntry) -> EntryKey:
        """Return the dependency graph key for ``entry``."""

        return (entry.addon.name, entry.source)

    def _dependency_key(
        self,
        entry: ResourceEntry,
        dependency: str,
        addon_names: dict[str, str],
    ) -> EntryKey:
        """Return the entry key addressed by one ``depends_on`` value."""

        if ":" in dependency:
            addon_ref, source = dependency.split(":", 1)
            return (addon_names.get(addon_ref, addon_ref), source)
        return (entry.addon.name, dependency)


@dataclass(slots=True)
class ResourceRow:
    """One normalized resource row."""

    entry: ResourceEntry
    """Entry that contributed this row."""

    model_label: str
    """Django model label targeted by this row."""

    xref: str
    """Addon-local external row key."""

    values: dict[str, Any]
    """Model field values to import."""

    @classmethod
    def from_record(
        cls,
        entry: ResourceEntry,
        record: Mapping[str, Any],
        *,
        index: int,
        fallback_model: str | None = None,
    ) -> ResourceRow:
        """Return a normalized row from parsed file data."""

        payload = dict(record)
        raw_model = payload.get("model") or fallback_model or entry.infer_model_label()
        raw_xref = payload.get("_xref") or payload.get("xref")
        if not isinstance(raw_xref, str) or not raw_xref.strip():
            raise ResourceLoadError(f"{entry.display} row {index}: missing _xref")
        return cls(
            entry=entry,
            model_label=str(raw_model),
            xref=raw_xref.strip(),
            values=cls._values_for(payload),
        )

    @property
    def dataset_row(self) -> dict[str, Any]:
        """Return the row as an import-export dataset mapping."""

        return {**self.values, "_xref": self.xref}

    @staticmethod
    def _values_for(payload: dict[str, Any]) -> dict[str, Any]:
        """Return model field values from one resource row payload."""

        if "fields" in payload:
            fields_value = payload["fields"]
            if not isinstance(fields_value, Mapping):
                raise ImproperlyConfigured("resource row fields must map names")
            reserved = RESERVED_ROW_KEYS & set(fields_value)
            if reserved:
                raise ImproperlyConfigured(
                    f"resource row fields cannot contain reserved keys: {', '.join(sorted(reserved))}"
                )
            return dict(fields_value)
        return {key: value for key, value in payload.items() if key not in RESERVED_ROW_KEYS}


@dataclass(slots=True)
class ResourceGroup:
    """Rows from one entry that target one model."""

    entry: ResourceEntry
    """Resource entry that supplied the rows."""

    model: type[models.Model]
    """Django model imported by this group."""

    rows: list[ResourceRow]
    """Rows loaded into the target model."""

    def to_dataset(self) -> tablib.Dataset:
        """Return this group as a tablib dataset."""

        headers = self._headers()
        dataset = tablib.Dataset(headers=headers)
        for row in self.rows:
            values = row.dataset_row
            dataset.append([values.get(header) for header in headers])
        return dataset

    def _headers(self) -> list[str]:
        """Return stable dataset headers preserving source row order."""

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
    """Number of resource files checked."""

    checked_rows: int
    """Number of resource rows checked."""


@dataclass(slots=True)
class LoadResult:
    """Counts returned by a resource load."""

    created: int
    """Rows created during the load."""

    updated: int
    """Rows updated during the load."""

    skipped: int
    """Rows skipped because the ledger already matched."""

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[RowResult],
        *,
        initial: LoadResult | None = None,
    ) -> LoadResult:
        """Return load counts tallied from import-export row results."""

        created = initial.created if initial is not None else 0
        updated = initial.updated if initial is not None else 0
        skipped = initial.skipped if initial is not None else 0
        for row in rows:
            if row.import_type == RowResult.IMPORT_TYPE_NEW:
                created += 1
            elif row.import_type == RowResult.IMPORT_TYPE_UPDATE:
                updated += 1
            elif row.import_type == RowResult.IMPORT_TYPE_SKIP:
                skipped += 1
        return cls(created=created, updated=updated, skipped=skipped)

    @property
    def loaded(self) -> int:
        """Return the number of created or updated rows."""

        return self.created + self.updated
