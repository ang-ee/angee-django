"""Declared resource sources and native import datasets."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypeAlias

import tablib
import yaml
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from import_export.results import Result, RowResult

from angee.addons import addon_contract
from angee.resources import sources
from angee.resources.exceptions import ResourceLoadError
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

ROW_KIND = "rows"
"""Default entry kind: rows imported into a Django model through the ledger."""

GRANT_KIND = "grants"
"""Entry kind whose rows the loader materializes into REBAC relationship tuples."""

ENTRY_KINDS = frozenset({ROW_KIND, GRANT_KIND})
"""Every resource entry kind the loader knows how to materialize."""

GRANT_ROW_KEYS = ("resource", "relation", "subject")
"""The three fields one declarative grant row must carry, in tuple direction."""

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


def resource_manifest_for(app_config: AppConfig) -> dict[str, tuple[dict[str, Any], ...]]:
    """Return normalized resource declarations keyed by tier for one addon."""

    contract = addon_contract(app_config)
    resources = contract.resources if contract is not None else {}
    manifest: dict[str, tuple[dict[str, Any], ...]] = {tier: () for tier in ResourceTier.values}
    for raw_tier, declarations in resources.items():
        tier = ResourceTier.from_value(raw_tier)
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
    """Return one normalized resource entry dictionary.

    The source key (``path``, or ``url`` when an addon like ``integrate`` configures
    it) selects a configured :class:`~angee.resources.sources.ResourceSource` that
    normalizes the value; the rest of the mapping is carried through as entry fields.
    """

    if isinstance(declaration, str | Path):
        return {"path": sources.normalize_path(app_config, declaration)}
    if not isinstance(declaration, Mapping):
        raise ImproperlyConfigured(f"{declaration!r} is not a resource path or mapping")

    source_key = _source_key(declaration)
    entry: dict[str, Any] = {str(key): declaration[key] for key in declaration if key not in sources.source_keys()}
    entry[source_key] = sources.get_source(source_key).normalize(app_config, declaration[source_key])
    if "depends_on" in entry:
        entry["depends_on"] = _normalize_depends_on(entry["depends_on"])
    if "adopt" in entry:
        entry["adopt"] = _resource_adopt_value(entry["adopt"])
    if "model" in entry and entry["model"] is not None:
        entry["model"] = str(entry["model"])
    if "encoding" in entry:
        entry["encoding"] = str(entry["encoding"] or "utf-8")
    return entry


def _source_key(declaration: Mapping[str, Any]) -> str:
    """Return the single configured source key named by ``declaration``, or raise."""

    present = [key for key in sources.source_keys() if declaration.get(key) is not None]
    if len(present) != 1:
        raise ImproperlyConfigured(
            f"resource entry {dict(declaration)!r} must set exactly one source of {sorted(sources.source_keys())}"
        )
    return present[0]


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


def _normalize_depends_on(value: object) -> tuple[str, ...]:
    """Return dependency keys, treating a bare string as a single key."""

    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Iterable):
        raise ImproperlyConfigured("depends_on must be a string or iterable of strings")
    return tuple(str(item) for item in value)


def _entry_kind(value: object) -> str:
    """Return a validated resource entry kind (``rows`` or ``grants``)."""

    kind = str(value)
    if kind not in ENTRY_KINDS:
        expected = ", ".join(sorted(ENTRY_KINDS))
        raise ImproperlyConfigured(f"Unknown resource entry kind {kind!r}; expected one of {expected}")
    return kind


@dataclass(slots=True)
class ResourceEntry:
    """One local or remote resource file declared by an addon."""

    addon: _ResourceAddon
    """Addon that owns this resource declaration."""

    tier: str
    """Normalized resource tier value."""

    source_key: str = "path"
    """Registered source key (``path`` for a local file; ``url`` once integrate adds it)."""

    source_value: str = ""
    """The source's stored value — an addon-relative path, a URL, etc."""

    model: str | None = None
    """Optional fallback model label for every row in the file."""

    kind: str = ROW_KIND
    """Materialization kind: ``rows`` (model rows) or ``grants`` (REBAC tuples)."""

    encoding: str = "utf-8"
    """Text encoding used when reading the materialized file."""

    depends_on: tuple[str, ...] = ()
    """Resource source keys that must load before this entry."""

    adopt: AdoptDeclaration = False
    """Unique field(s) used for adoption; ``True`` infers one unique field."""

    publish: bool = False
    """Whether loaded rows should be published by a target-model resource hook."""

    _groups: tuple[ResourceGroup, ...] | None = field(
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
        """Return an entry from a resource declaration, resolving its source type."""

        source_key = _source_key(declaration)
        return cls(
            addon=addon,
            tier=tier,
            source_key=source_key,
            source_value=str(declaration[source_key]),
            model=declaration.get("model"),
            kind=_entry_kind(declaration.get("kind", ROW_KIND)),
            encoding=declaration.get("encoding", "utf-8"),
            depends_on=declaration.get("depends_on", ()),
            adopt=_resource_adopt_value(declaration["adopt"]) if "adopt" in declaration else False,
            publish=bool(declaration.get("publish", False)),
        )

    @property
    def source(self) -> str:
        """Return this entry's stable source value (path, URL, …)."""

        return self.source_value

    @property
    def key(self) -> EntryKey:
        """Return the dependency and exclusion key for this resource entry."""

        return (self.addon.name, self.source)

    @property
    def display(self) -> str:
        """Return an owner-qualified name for diagnostics."""

        return f"{self.addon.name}:{self.source}"

    def materialize(self) -> Path:
        """Return the local file path, materializing the source once."""

        if self._local_path is None:
            self._local_path = sources.get_source(self.source_key).materialize(self)
        return self._local_path

    def read_groups(self) -> tuple[ResourceGroup, ...]:
        """Normalize this entry once into native datasets by target model."""

        if self._groups is not None:
            return self._groups
        records, file_model = self._read_records()
        self._check_model_conflict(file_model)
        if not records:
            self._groups = ()
            return self._groups
        fallback_model = self.model or file_model
        if isinstance(records, tablib.Dataset):
            headers = records.headers or []
            if not {"model", "fields", "_meta"} & set(headers) and len(headers) == len(set(headers)):
                self._groups = (self._tabular_group(records, fallback_model),)
                return self._groups
        groups: dict[tuple[str, str], ResourceGroup] = {}
        for index, record in enumerate(self._record_mappings(records), start=1):
            model_label = record.get("model") or fallback_model
            if not model_label:
                fallback_model = self.infer_model_label()
                model_label = fallback_model
            label = str(model_label)
            key = self._model_key(label)
            group = groups.get(key)
            if group is None:
                group = ResourceGroup(self, label, tablib.Dataset(headers=["_xref"]), [])
                groups[key] = group
            raw_xref = record.get("_xref") or record.get("xref")
            xref = self._normalized_xref(raw_xref, index)
            values = self._values_for(record)
            for name in values:
                if name not in group.dataset.headers:
                    group.dataset.append_col([None] * len(group.dataset), header=name)
            group.dataset.append([xref, *(values.get(name) for name in group.dataset.headers[1:])])
            group.source_rows.append(index)
        self._groups = tuple(groups.values())
        return self._groups

    def _tabular_group(self, dataset: tablib.Dataset, model_label: str | None) -> ResourceGroup:
        """Keep the parsed rectangular Dataset and normalize its xref column."""

        values = dataset["_xref"] if "_xref" in dataset.headers else [None] * len(dataset)
        aliases = dataset["xref"] if "xref" in dataset.headers else [None] * len(dataset)
        xrefs = [
            self._normalized_xref(value or alias, index)
            for index, (value, alias) in enumerate(zip(values, aliases, strict=True), start=1)
        ]
        for column in ("_xref", "xref"):
            if column in dataset.headers:
                del dataset[column]
        dataset.insert_col(0, xrefs, header="_xref")
        return ResourceGroup(
            self,
            model_label or self.infer_model_label(),
            dataset,
            list(range(1, len(dataset) + 1)),
        )

    def _normalized_xref(self, value: Any, index: int) -> str:
        """Return the one technical identity column with source diagnostics."""

        if not isinstance(value, str) or not value.strip():
            raise ResourceLoadError(f"{self.display} row {index}: missing _xref")
        return value.strip()

    @staticmethod
    def _record_mappings(records: list[dict[str, Any]] | tablib.Dataset) -> Iterable[Mapping[str, Any]]:
        """Interpret heterogeneous tabular records only when partitioning needs it."""

        if isinstance(records, tablib.Dataset):
            return (dict(zip(records.headers, row, strict=True)) for row in records)
        return records

    def read_grant_rows(self) -> tuple[GrantRow, ...]:
        """Return parsed grant rows for a ``kind = "grants"`` entry.

        A grant file is a flat list of ``{resource, relation, subject}`` rows; it
        declares no model (the loader materializes tuples, not model rows), so a
        model in the entry or file metadata is a declaration error.
        """

        records, file_model = self._read_records()
        if self.model or file_model:
            raise ResourceLoadError(f"{self.display}: a grants entry declares no model")
        return tuple(
            GrantRow.from_record(self, record, index=index)
            for index, record in enumerate(self._record_mappings(records), start=1)
        )

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

    def _read_records(self) -> tuple[list[dict[str, Any]] | tablib.Dataset, str | None]:
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

        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                raise ImproperlyConfigured(f"{self.display} row {index}: row must be a mapping")
        return rows, file_model

    def _read_tabular(
        self,
        path: Path,
        file_format: str,
    ) -> tablib.Dataset:
        """Read CSV or TSV directly into its native import dataset."""

        content = path.read_text(encoding=self.encoding)
        dataset = tablib.Dataset()
        if not content.strip():
            return dataset
        try:
            dataset.load(content, format=file_format)
        except Exception as error:
            raise ImproperlyConfigured(f"{self.display} could not be parsed as {file_format}") from error
        return dataset

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

    @staticmethod
    def _values_for(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return model field values from one resource row payload."""

        if "fields" in payload:
            fields_value = payload["fields"]
            if not isinstance(fields_value, Mapping):
                raise ImproperlyConfigured("resource row fields must map names")
            # The nested form carries the model label in `_meta`, so `model` under an
            # explicit `fields:` map is a real field name (e.g. ``agents.Agent.model``);
            # only the truly-structural row keys are rejected here.
            reserved = (RESERVED_ROW_KEYS - {"model"}) & set(fields_value)
            if reserved:
                raise ImproperlyConfigured(
                    f"resource row fields cannot contain reserved keys: {', '.join(sorted(reserved))}"
                )
            return dict(fields_value)
        return {key: value for key, value in payload.items() if key not in RESERVED_ROW_KEYS}


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
            key = entry.key
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
class GrantRow:
    """One declarative REBAC grant row, in ``resource <- relation <- subject`` direction."""

    entry: ResourceEntry
    """Entry that contributed this grant row."""

    resource: str
    """The grant resource: a ``<ns>/type:<const-id>`` literal or a row xref."""

    relation: str
    """The relation granted on the resource (e.g. ``member``, ``direct_member``)."""

    subject: str
    """The subject granted: a user/row xref, a ``<ns>/role:<id>#member`` literal, or ``*``."""

    index: int
    """1-based position of this row within its source file, for diagnostics."""

    @classmethod
    def from_record(
        cls,
        entry: ResourceEntry,
        record: Mapping[str, Any],
        *,
        index: int,
    ) -> GrantRow:
        """Return one validated grant row from parsed file data."""

        unknown = sorted(set(record) - set(GRANT_ROW_KEYS))
        if unknown:
            raise ResourceLoadError(
                f"{entry.display} grant {index}: unknown field(s) {', '.join(unknown)}; "
                f"expected {', '.join(GRANT_ROW_KEYS)}"
            )
        values: dict[str, str] = {}
        for key in GRANT_ROW_KEYS:
            value = record.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ResourceLoadError(f"{entry.display} grant {index}: missing {key}")
            values[key] = value.strip()
        return cls(entry=entry, index=index, **values)


@dataclass(slots=True)
class GrantGroup:
    """Grant rows from one ``kind = "grants"`` entry."""

    entry: ResourceEntry
    """Resource entry that supplied the grant rows."""

    rows: tuple[GrantRow, ...]
    """Grant rows the loader materializes into REBAC relationship tuples."""


@dataclass
class ResourceGroup:
    """One source/model dataset with the source indexes needed for errors."""

    entry: ResourceEntry
    model_label: str
    dataset: tablib.Dataset
    source_rows: list[int]

    @cached_property
    def model(self) -> type[models.Model]:
        """Resolve once at import time; source inspection needs only the label."""

        return resolve_model(self.model_label)


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

    def with_result(self, result: Result) -> LoadResult:
        """Return this load count plus import-export's native result totals."""

        totals = result.totals
        return type(self)(
            created=self.created + totals[RowResult.IMPORT_TYPE_NEW],
            updated=self.updated + totals[RowResult.IMPORT_TYPE_UPDATE],
            skipped=self.skipped + totals[RowResult.IMPORT_TYPE_SKIP],
        )

    @property
    def loaded(self) -> int:
        """Return the number of created or updated rows."""

        return self.created + self.updated
