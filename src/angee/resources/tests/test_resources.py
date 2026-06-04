"""Tests for resource declarations, ordering, fetching, and loading."""

from __future__ import annotations

import socket
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, models
from rebac import system_context

from angee.base.models import AngeeModel
from angee.resources.entries import EntryGraph, ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.fetch import _PublicUrlRedirectHandler, fetch_url
from angee.resources.models import Resource
from angee.resources.widgets import resolve_xref


@dataclass(slots=True)
class Addon:
    """Small addon stand-in exposing normalized resource declarations."""

    name: str
    """Full dotted addon name."""

    label: str
    """Django app label."""

    path: str
    """Filesystem root for local resource files."""

    resource_manifest: Mapping[str, tuple[Mapping[str, Any], ...]]
    """Normalized resource manifest keyed by tier."""


def addon(
    tmp_path: Path,
    *,
    name: str = "tests.resource_addon",
    label: str = "resource_addon",
    manifest: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> Addon:
    """Return a resource addon rooted at ``tmp_path``."""

    return Addon(
        name=name,
        label=label,
        path=str(tmp_path),
        resource_manifest=manifest or {"master": (), "install": (), "demo": ()},
    )


def entry(
    tmp_path: Path,
    declaration: dict[str, Any],
    *,
    tier: str = "master",
    owner: Addon | None = None,
) -> ResourceEntry:
    """Return a resource entry for one declaration mapping."""

    return ResourceEntry.from_declaration(
        owner or addon(tmp_path),
        tier,
        declaration,
    )


def test_resource_entry_reads_structured_rows_and_fields(
    tmp_path: Path,
) -> None:
    """YAML resources can declare a model and field envelope."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "notes.yaml").write_text(
        "_meta:\n  model: base.ImportNote\nrows:\n  - _xref: n1\n    fields:\n      title: First\n",
        encoding="utf-8",
    )

    rows = entry(
        tmp_path,
        {"path": "resources/notes.yaml"},
    ).read_resource_rows()

    assert len(rows) == 1
    assert rows[0].model_label == "base.ImportNote"
    assert rows[0].xref == "n1"
    assert rows[0].dataset_row == {"_xref": "n1", "title": "First"}


def test_resource_entry_rejects_reserved_keys_in_structured_fields(
    tmp_path: Path,
) -> None:
    """Structured field envelopes cannot override loader-owned keys."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "notes.yaml").write_text(
        "_meta:\n  model: base.ImportNote\nrows:\n  - _xref: n1\n    fields:\n      _xref: n2\n      title: First\n",
        encoding="utf-8",
    )

    with pytest.raises(ImproperlyConfigured, match="reserved keys: _xref"):
        entry(
            tmp_path,
            {"path": "resources/notes.yaml"},
        ).read_resource_rows()


def test_resource_entry_rejects_model_conflicts(tmp_path: Path) -> None:
    """File metadata cannot disagree with the entry model."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "data.yaml").write_text(
        "_meta:\n  model: base.Other\nrows:\n  - _xref: a\n",
        encoding="utf-8",
    )

    with pytest.raises(ResourceLoadError, match="model conflict"):
        entry(
            tmp_path,
            {"path": "resources/data.yaml", "model": "base.ImportNote"},
        ).read_resource_rows()


def test_resource_entry_rejects_unsupported_formats(tmp_path: Path) -> None:
    """Only text formats owned by the resource loader are accepted."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "data.xlsx").write_bytes(b"binary")

    with pytest.raises(ImproperlyConfigured, match="unsupported format"):
        entry(tmp_path, {"path": "resources/data.xlsx"}).read_resource_rows()


def test_entry_graph_respects_same_and_cross_addon_dependencies(
    tmp_path: Path,
) -> None:
    """Dependency edges order resources across selected addons."""

    upstream_addon = addon(tmp_path, name="tests.upstream", label="upstream")
    downstream_addon = addon(
        tmp_path,
        name="tests.downstream",
        label="downstream",
    )
    first = ResourceEntry.from_declaration(
        upstream_addon,
        "master",
        {"path": "a.csv"},
    )
    second = ResourceEntry.from_declaration(
        downstream_addon,
        "master",
        {"path": "b.csv", "depends_on": ("upstream:a.csv",)},
    )
    third = ResourceEntry.from_declaration(
        downstream_addon,
        "master",
        {"path": "c.csv", "depends_on": ("b.csv",)},
    )

    ordered = EntryGraph.from_entries([third, second, first]).ordered()

    assert [item.source for item in ordered] == ["a.csv", "b.csv", "c.csv"]


def test_resource_entry_uses_normalized_dependency_tuple(
    tmp_path: Path,
) -> None:
    """Resource entries consume dependency tuples normalized by AppConfig."""

    resource_entry = ResourceEntry.from_declaration(
        addon(tmp_path),
        "master",
        {"path": "b.csv", "depends_on": ("a.csv",)},
    )

    assert resource_entry.depends_on == ("a.csv",)


def test_entry_graph_detects_cycles(tmp_path: Path) -> None:
    """Dependency cycles fail before any rows load."""

    owner = addon(tmp_path)
    first = ResourceEntry.from_declaration(
        owner,
        "master",
        {"path": "a.csv", "depends_on": ("b.csv",)},
    )
    second = ResourceEntry.from_declaration(
        owner,
        "master",
        {"path": "b.csv", "depends_on": ("a.csv",)},
    )

    with pytest.raises(ResourceLoadError, match="cycle"):
        EntryGraph.from_entries([first, second]).ordered()


def test_fetch_url_rejects_non_http_urls() -> None:
    """Remote resources are limited to http and https URLs."""

    with pytest.raises(ResourceLoadError, match="http or https"):
        fetch_url("file:///private/resource.csv")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/data.csv",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00:ec2::254]/data.csv",
        "http://10.0.0.5/data.csv",
    ],
)
def test_fetch_url_rejects_ssrf_unsafe_targets(url: str) -> None:
    """Fetch refuses hosts that resolve to loopback, private, link-local, or metadata IPs."""

    with pytest.raises(ResourceLoadError, match="public IP"):
        fetch_url(url)


def test_fetch_url_rejects_redirect_to_non_http_url() -> None:
    """Redirected resource URLs are limited to http and https targets."""

    request = urllib.request.Request("https://example.test/data.csv")
    handler = _PublicUrlRedirectHandler()

    with pytest.raises(ResourceLoadError, match="http or https"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "file:///private/resource.csv",
        )


def test_fetch_url_caches_by_full_url(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetched URL is read from cache on the second request."""

    settings.ANGEE_DATA_DIR = tmp_path
    calls: list[str] = []

    class Response:
        """Tiny context manager matching urllib response use."""

        def read(self) -> bytes:
            """Return CSV payload bytes."""

            return b"_xref,username\nu1,alice\n"

        def __enter__(self) -> Response:
            """Return the response object for context-manager use."""

            return self

        def __exit__(self, *exc: object) -> None:
            """Close the fake response."""

            return None

    class Opener:
        """Tiny opener matching urllib's opener surface."""

        def open(self, request: Any) -> Response:
            """Record the requested URL and return a fake response."""

            calls.append(request.full_url)
            return Response()

    def build_opener(*handlers: object) -> Opener:
        """Return an opener with redirect scheme checks installed."""

        assert any(isinstance(handler, _PublicUrlRedirectHandler) for handler in handlers)
        return Opener()

    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    # The pre-flight SSRF guard resolves the host; pin it to a public address.
    monkeypatch.setattr(
        "angee.base.net.socket.getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )

    first = fetch_url("https://example.test/data.csv")
    second = fetch_url("https://example.test/data.csv")

    assert first == second
    assert first.suffix == ".csv"
    assert first.read_bytes() == b"_xref,username\nu1,alice\n"
    assert calls == ["https://example.test/data.csv"]


def test_resource_unique_constraint_is_addon_xref_pair() -> None:
    """The ledger identity is the source addon and row xref."""

    constraints = {
        constraint.name: constraint
        for constraint in Resource.Meta.constraints
        if isinstance(constraint, models.UniqueConstraint)
    }

    assert constraints["%(app_label)s_resource_addon_xref"].fields == (
        "source_addon",
        "xref",
    )


@pytest.mark.django_db(transaction=True)
def test_resolve_xref_accepts_addon_label_alias() -> None:
    """A label-form xref resolves to the canonical addon ledger row."""

    class ResolveExactTarget(models.Model):
        """Target model resolved by exact xref tests."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResolveExactLedger(models.Model):
        """Ledger model without the production uniqueness constraint."""

        source_addon = models.CharField(max_length=200)
        xref = models.CharField(max_length=160)
        target_model = models.CharField(max_length=120)
        target_id = models.CharField(max_length=120, blank=True, default="")

        class Meta:
            """Django model options for the test ledger."""

            app_label = "base"

    models_to_create: tuple[type[models.Model], ...] = (
        ResolveExactTarget,
        ResolveExactLedger,
    )
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        target = ResolveExactTarget.objects.create(name="target")
        ResolveExactLedger.objects.create(
            source_addon="tests.resource_addon",
            xref="target",
            target_model="base.ResolveExactTarget",
            target_id=str(target.pk),
        )

        resolved = resolve_xref(
            "resource_addon.target",
            ResolveExactLedger,
            {
                "resource_addon": "tests.resource_addon",
                "tests.resource_addon": "tests.resource_addon",
            },
        )

        assert resolved == target
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resolve_xref_reports_ambiguous_source_rows() -> None:
    """Duplicate addon/xref ledger rows fail before choosing a target."""

    class ResolveAmbiguousTargetA(models.Model):
        """First target model for ambiguous xref tests."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResolveAmbiguousTargetB(models.Model):
        """Second target model for ambiguous xref tests."""

        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResolveAmbiguousLedger(models.Model):
        """Ledger model without the production uniqueness constraint."""

        source_addon = models.CharField(max_length=200)
        xref = models.CharField(max_length=160)
        target_model = models.CharField(max_length=120)
        target_id = models.CharField(max_length=120, blank=True, default="")

        class Meta:
            """Django model options for the test ledger."""

            app_label = "base"

    models_to_create: tuple[type[models.Model], ...] = (
        ResolveAmbiguousTargetA,
        ResolveAmbiguousTargetB,
        ResolveAmbiguousLedger,
    )
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        first = ResolveAmbiguousTargetA.objects.create(name="first")
        second = ResolveAmbiguousTargetB.objects.create(name="second")
        ResolveAmbiguousLedger.objects.create(
            source_addon="tests.resource_addon",
            xref="shared",
            target_model="base.ResolveAmbiguousTargetA",
            target_id=str(first.pk),
        )
        ResolveAmbiguousLedger.objects.create(
            source_addon="tests.resource_addon",
            xref="shared",
            target_model="base.ResolveAmbiguousTargetB",
            target_id=str(second.pk),
        )

        with pytest.raises(ValueError, match="ambiguous xref"):
            resolve_xref(
                "tests.resource_addon.shared",
                ResolveAmbiguousLedger,
                {"tests.resource_addon": "tests.resource_addon"},
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_manager_loads_rows_and_resolves_xrefs(
    tmp_path: Path,
) -> None:
    """Resource rows load with label-form FK xrefs on a fresh ledger."""

    class ImportUser(AngeeModel):
        """User-like model loaded from resources."""

        username = models.CharField(max_length=40, unique=True)
        first_name = models.CharField(max_length=40, blank=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ImportNote(AngeeModel):
        """Note-like model referencing a resource-loaded user."""

        title = models.CharField(max_length=80)
        tags = models.JSONField(default=list, blank=True)
        created_by = models.ForeignKey(ImportUser, on_delete=models.CASCADE)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ResourceLedger(Resource):
        """Concrete resource ledger for the test database."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    owner = _write_resource_files(tmp_path)
    models_to_create = (ImportUser, ImportNote, ResourceLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        result = ResourceLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )

        with system_context(reason="resource load assertions"):
            alice = ImportUser.objects.get(username="alice")
            note = ImportNote.objects.get(title="Framework map")
        ledger_xrefs = set(ResourceLedger.objects.values_list("xref", flat=True))

        assert result.created == 2
        assert result.loaded == 2
        assert alice.first_name == "Alice"
        assert note.created_by == alice
        assert note.tags == ["composition", "resources"]
        assert ledger_xrefs == {"user_alice", "note_framework_map"}

        second = ResourceLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )
        assert second.created == 0
        assert second.updated == 0
        assert second.skipped == 2
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_load_rejects_existing_xref_for_another_model(
    tmp_path: Path,
) -> None:
    """A later load cannot reuse an addon xref for another target model."""

    class CollisionUser(AngeeModel):
        """First target model for ledger collision tests."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class CollisionNote(AngeeModel):
        """Second target model for ledger collision tests."""

        title = models.CharField(max_length=80)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class CollisionLedger(Resource):
        """Concrete resource ledger for collision tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.collisionuser.csv").write_text(
        "_xref,username\nshared,alice\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.collisionnote.csv").write_text(
        "_xref,title\nshared,Same xref\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": ({"path": "resources/010_base.collisionuser.csv"},),
            "install": ({"path": "resources/020_base.collisionnote.csv"},),
            "demo": (),
        },
    )

    models_to_create = (CollisionUser, CollisionNote, CollisionLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        CollisionLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.MASTER],
        )

        with pytest.raises(ResourceLoadError, match="xref collision"):
            CollisionLedger.objects.load_addons(
                (owner,),
                tiers=[Resource.Tier.INSTALL],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_validate_cleans_rows_and_resolves_xrefs(
    tmp_path: Path,
) -> None:
    """Validation runs widget cleaning inside a rolled-back import."""

    class ValidateNote(AngeeModel):
        """Note-like model with an FK that must resolve by xref."""

        title = models.CharField(max_length=80)
        created_by = models.ForeignKey(
            "base.ValidateUser",
            on_delete=models.CASCADE,
        )

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ValidateUser(AngeeModel):
        """User-like model referenced by validation rows."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ValidateLedger(Resource):
        """Concrete resource ledger for validation tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.validatenote.yaml").write_text(
        "- _xref: note\n  title: Broken reference\n  created_by: tests.resource_addon.missing\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": ({"path": "resources/010_base.validatenote.yaml"},),
            "install": (),
            "demo": (),
        },
    )

    models_to_create = (ValidateUser, ValidateNote, ValidateLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        with pytest.raises(ResourceLoadError, match="unresolved xref"):
            ValidateLedger.objects.validate_addons(
                (owner,),
                tiers=[Resource.Tier.MASTER],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_is_opt_in(tmp_path: Path) -> None:
    """Rows without ledgers adopt existing targets only when requested."""

    class AdoptUser(AngeeModel):
        """Model with a unique natural key suitable for adoption."""

        username = models.CharField(max_length=40, unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class AdoptLedger(Resource):
        """Concrete resource ledger for adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.adoptuser.csv").write_text(
        "_xref,username\nexisting,alice\n",
        encoding="utf-8",
    )
    manifest: Mapping[str, tuple[Mapping[str, Any], ...]] = {
        "master": (),
        "install": (
            {
                "path": "resources/010_base.adoptuser.csv",
                "adopt": True,
            },
        ),
        "demo": (),
    }
    owner = addon(tmp_path, manifest=manifest)
    no_adopt = addon(
        tmp_path,
        manifest={
            **manifest,
            "install": ({"path": "resources/010_base.adoptuser.csv"},),
        },
    )

    models_to_create = (AdoptUser, AdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        AdoptUser.objects.create(username="alice")
        with pytest.raises((IntegrityError, ResourceLoadError)):
            AdoptLedger.objects.load_addons(
                (no_adopt,),
                tiers=[Resource.Tier.INSTALL],
            )

        result = AdoptLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.created == 0
        assert result.updated == 0
        assert result.skipped == 1
        with system_context(reason="resource adoption assertions"):
            assert AdoptUser.objects.count() == 1
        assert AdoptLedger.objects.get(xref="existing").target_id
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_uses_explicit_unique_field(
    tmp_path: Path,
) -> None:
    """A named adoption field resolves ambiguity between unique fields."""

    class ExplicitAdoptUser(AngeeModel):
        """Model with multiple unique fields."""

        username = models.CharField(max_length=40, unique=True)
        email = models.EmailField(unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class ExplicitAdoptLedger(Resource):
        """Concrete resource ledger for explicit adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.explicitadoptuser.csv").write_text(
        "_xref,username,email\nexisting,alice,alice@example.test\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.explicitadoptuser.csv",
                    "adopt": "username",
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (ExplicitAdoptUser, ExplicitAdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        ExplicitAdoptUser.objects.create(
            username="alice",
            email="alice@example.test",
        )

        result = ExplicitAdoptLedger.objects.load_addons(
            (owner,),
            tiers=[Resource.Tier.INSTALL],
        )

        assert result.skipped == 1
        with system_context(reason="explicit adoption assertions"):
            assert ExplicitAdoptUser.objects.count() == 1
        assert ExplicitAdoptLedger.objects.get(xref="existing").target_id
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_resource_adoption_rejects_ambiguous_unique_fields(
    tmp_path: Path,
) -> None:
    """Implicit adoption fails when a row has multiple unique candidates."""

    class AmbiguousAdoptUser(AngeeModel):
        """Model with multiple unique fields."""

        username = models.CharField(max_length=40, unique=True)
        email = models.EmailField(unique=True)

        class Meta:
            """Django model options for the test model."""

            app_label = "base"

    class AmbiguousAdoptLedger(Resource):
        """Concrete resource ledger for ambiguous adoption tests."""

        class Meta(Resource.Meta):
            """Django model options for the test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.ambiguousadoptuser.csv").write_text(
        "_xref,username,email\nexisting,alice,alice@example.test\n",
        encoding="utf-8",
    )
    owner = addon(
        tmp_path,
        manifest={
            "master": (),
            "install": (
                {
                    "path": "resources/010_base.ambiguousadoptuser.csv",
                    "adopt": True,
                },
            ),
            "demo": (),
        },
    )

    models_to_create = (AmbiguousAdoptUser, AmbiguousAdoptLedger)
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        AmbiguousAdoptUser.objects.create(
            username="alice",
            email="alice@example.test",
        )

        with pytest.raises(ImproperlyConfigured, match="multiple unique"):
            AmbiguousAdoptLedger.objects.load_addons(
                (owner,),
                tiers=[Resource.Tier.INSTALL],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)


def _write_resource_files(tmp_path: Path) -> Addon:
    """Write a small resource set and return its declaring addon."""

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.importuser.csv").write_text(
        "_xref,username,first_name\nuser_alice,alice,Alice\n",
        encoding="utf-8",
    )
    (resource_dir / "020_base.importnote.yaml").write_text(
        "- _xref: note_framework_map\n"
        "  title: Framework map\n"
        "  created_by: resource_addon.user_alice\n"
        "  tags:\n"
        "    - composition\n"
        "    - resources\n",
        encoding="utf-8",
    )
    return addon(
        tmp_path,
        manifest={
            "master": (
                {"path": "resources/010_base.importuser.csv"},
                {"path": "resources/020_base.importnote.yaml"},
            ),
            "install": (),
            "demo": (),
        },
    )
