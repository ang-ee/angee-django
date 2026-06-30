"""Shared pytest infrastructure for source-addon tests."""

from __future__ import annotations

import itertools
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import reversion
from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection, models
from django.test import RequestFactory
from rebac import actor_context, system_context

from angee.addons import AddonContract
from angee.agents.backends import InferenceBackend, InferenceModelSpec
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.iam_integrate_oidc.models import OAuthClientOidc as AbstractOAuthClientOidc
from angee.integrate.credentials import CredentialKind
from angee.integrate.models import Credential as AbstractCredential
from angee.integrate.models import ExternalAccount as AbstractExternalAccount
from angee.integrate.models import Integration as AbstractIntegration
from angee.integrate.models import OAuthClient as AbstractOAuthClient
from angee.integrate.models import Repository as AbstractRepository
from angee.integrate.models import Source as AbstractSource
from angee.integrate.models import Template as AbstractTemplate
from angee.integrate.models import VcsBridge as AbstractVcsBridge
from angee.integrate.models import Vendor as AbstractVendor
from angee.integrate.models import WebhookSubscription as AbstractWebhookSubscription
from angee.integrate.vcs.backend import RepoDescriptor, TreeEntry, VCSBackend
from angee.knowledge.models import Link as AbstractLink
from angee.knowledge.models import MarkdownPage as AbstractMarkdownPage
from angee.knowledge.models import Page as AbstractPage
from angee.knowledge.models import Vault as AbstractVault
from angee.platform.models import Addon as AbstractAddon
from angee.platform.models import PlatformExplorer as AbstractPlatformExplorer
from angee.platform_integrate_vcs.models import AddonCatalog as AbstractAddonCatalog
from angee.platform_integrate_vcs.models import CatalogProvenance as AbstractCatalogProvenance
from angee.storage.models import Backend as AbstractStorageBackend
from angee.storage.models import Drive as AbstractDrive
from angee.storage.models import File as AbstractFile
from angee.storage.models import Folder as AbstractFolder
from angee.storage.models import MimeType as AbstractMimeType


class OAuthClient(AbstractOAuthClient, AbstractOAuthClientOidc):
    """Concrete OAuth client used by source-addon tests.

    Composes the OIDC login extension (``OAuthClientOidc``) the way the composer
    folds it onto the real ``OAuthClient`` — so the one table carries both the OAuth
    substrate and the OIDC login fields on one concrete model.
    """

    class Meta(AbstractOAuthClient.Meta):
        """Django model options for the canonical test OAuth client."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_oauth_client"
        rebac_resource_type = "integrate/oauth_client"
        rebac_id_attr = "sqid"


class ExternalAccount(AbstractExternalAccount):
    """Concrete integration external account used by source-addon tests."""

    class Meta(AbstractExternalAccount.Meta):
        """Django model options for the canonical test external account."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_external_account"
        rebac_resource_type = "integrate/external_account"
        rebac_id_attr = "sqid"


class Credential(AbstractCredential):
    """Concrete integration credential used by source-addon tests."""

    class Meta(AbstractCredential.Meta):
        """Django model options for the canonical test credential."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_credential"
        rebac_resource_type = "integrate/credential"
        rebac_id_attr = "sqid"


class Vendor(AbstractVendor):
    """Concrete integration vendor catalogue row used by source-addon tests."""

    class Meta(AbstractVendor.Meta):
        """Django model options for the canonical test vendor."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_vendor"
        rebac_resource_type = "integrate/vendor"
        rebac_id_attr = "sqid"


class Integration(AbstractIntegration):
    """Concrete integration used by source-addon tests."""

    class Meta(AbstractIntegration.Meta):
        """Django model options for the canonical test integration."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_integration"
        rebac_resource_type = "integrate/integration"
        rebac_id_attr = "sqid"


class WebhookSubscription(AbstractWebhookSubscription):
    """Concrete integrate webhook subscription used by source-addon tests."""

    class Meta(AbstractWebhookSubscription.Meta):
        """Django model options for the canonical test webhook subscription."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_webhook_subscription"
        rebac_resource_type = "integrate/webhook_subscription"
        rebac_id_attr = "sqid"


class Vault(AbstractVault):
    """Concrete knowledge vault used by source-addon tests."""

    class Meta(AbstractVault.Meta):
        """Django model options for the canonical test vault."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_vault"
        rebac_resource_type = "knowledge/vault"
        rebac_id_attr = "sqid"


class Page(AbstractPage):
    """Concrete knowledge page used by source-addon tests."""

    class Meta(AbstractPage.Meta):
        """Django model options for the canonical test page."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_page"
        rebac_resource_type = "knowledge/page"
        rebac_id_attr = "sqid"


@reversion.register(fields=("body",))
class MarkdownPage(AbstractMarkdownPage):
    """Concrete knowledge markdown sidecar used by source-addon tests.

    Registered with django-reversion the way the composer registers the
    emitted runtime model (``RevisionMixin.angee_model_decorators``).
    """

    class Meta(AbstractMarkdownPage.Meta):
        """Django model options for the canonical test markdown page."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_markdown_page"
        rebac_resource_type = "knowledge/markdown_page"
        rebac_id_attr = "sqid"


IAM_CONNECTION_TEST_MODELS = (OAuthClient, ExternalAccount, Credential)
"""Concrete integration connection models created on demand by connection test fixtures."""

INTEGRATE_TEST_MODELS = (Vendor, Integration)
"""Concrete integration catalogue/integration models created on demand by integrate fixtures."""


class VcsBridge(Integration, AbstractVcsBridge):
    """Concrete VCS bridge used by source-addon tests.

    ``angee.integrate.schema`` binds the VCS console types at import time via
    ``apps.get_model``, so the concrete models live here (imported before any test
    module) rather than in a single test file — otherwise importing the schema from
    one test depends on another test having been collected first.
    """

    class Meta(AbstractVcsBridge.Meta):
        """Django model options for the canonical test VCS bridge."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_vcs_bridge"
        rebac_resource_type = "integrate/vcs_bridge"
        rebac_id_attr = "sqid"


class Repository(AbstractRepository):
    """Concrete repository used by source-addon tests."""

    class Meta(AbstractRepository.Meta):
        """Django model options for the canonical test repository."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_repository"
        rebac_resource_type = "integrate/repository"
        rebac_id_attr = "sqid"


class Source(AbstractSource):
    """Concrete source used by source-addon tests."""

    class Meta(AbstractSource.Meta):
        """Django model options for the canonical test source."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_source"
        rebac_resource_type = "integrate/source"
        rebac_id_attr = "sqid"


class Template(AbstractTemplate):
    """Concrete template used by source-addon tests."""

    source_kind = "template"

    class Meta(AbstractTemplate.Meta):
        """Django model options for the canonical test template."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_template"
        rebac_resource_type = "integrate/template"
        rebac_id_attr = "sqid"


VCS_TEST_MODELS = (VcsBridge, Repository, Source, Template)
"""Concrete VCS inventory models created on demand by VCS test fixtures."""


def make_integration(
    slug: str,
    *,
    kind: Any = CredentialKind.STATIC_TOKEN,
    material: dict[str, Any] | None = None,
    impl_class: str = "none",
    backend_class: str | None = None,
    model: type[Any] = Integration,
    **attrs: Any,
) -> Any:
    """Create the iam credential chain and an integration model row for tests.

    Builds owner → OAuth client → credential → vendor → model row. ``kind``/
    ``material`` pick the credential kind (default a static token); pass
    ``kind=CredentialKind.OAUTH`` for an OAuth-backed integration. ``model`` may
    be a concrete MTI child such as ``VcsBridge``; VCS child rows choose
    ``backend_class`` while parent-only integrations choose ``impl_class``.
    """

    if material is None:
        material = {"access_token": "token"} if kind == CredentialKind.OAUTH else {"api_key": "x"}
    user_model = get_user_model()
    with system_context(reason="test integrate integration setup"):
        user = user_model.objects.create_user(username=f"{slug}-owner", email=f"{slug}@example.com")
        oauth_client = OAuthClient.objects.create(
            slug=slug,
            display_name=slug.title(),
            client_id=f"{slug}-cid",
        )
        credential = Credential.objects.upsert_for_user(user, oauth_client, kind, material)
        vendor = Vendor.objects.create(slug=slug, display_name=slug.title())
        values = {
            "vendor": vendor,
            "credential": credential,
            "owner": user,
            "status": "active",
            **attrs,
        }
        field_names = {field.name for field in model._meta.fields}
        if "backend_class" in field_names:
            values["backend_class"] = backend_class or ("local" if impl_class == "none" else impl_class)
        else:
            values["impl_class"] = impl_class
        return model.objects.create(**values)


class StubVCSBackend(VCSBackend):
    """In-memory VCS backend for tests; canned data rides on ``VcsBridge.config``.

    Registered as the ``stub`` key in the test ``ANGEE_VCS_BACKEND_CLASSES`` so a
    ``VcsBridge(backend_class="stub")`` resolves to it. Each test injects
    ``stub_repos``/``stub_tree``/``stub_blobs`` through the bridge config.
    """

    def ls_repos(self, *, org: str = "") -> list[RepoDescriptor]:
        """Return the configured repositories (filtered to ``org`` when given)."""

        repos = [RepoDescriptor(**spec) for spec in self.bridge.config.get("stub_repos", [])]
        return [repo for repo in repos if not org or repo.org == org]

    def get_repo(self, name: str) -> RepoDescriptor:
        """Return one configured repository by name or raise."""

        for spec in self.bridge.config.get("stub_repos", []):
            if spec["name"] == name:
                return RepoDescriptor(**spec)
        raise FileNotFoundError(name)

    def search_repos(self, query: str, *, org: str = "") -> list[RepoDescriptor]:
        """Return configured repositories whose name contains ``query``."""

        return [repo for repo in self.ls_repos(org=org) if query in repo.name]

    def ls_tree(self, repository: Any, *, ref: str, path: str, recursive: bool = False) -> list[TreeEntry]:
        """Return the configured tree entries under ``path``."""

        del repository, ref, recursive
        prefix = path.strip("/")
        entries = [TreeEntry(**spec) for spec in self.bridge.config.get("stub_tree", [])]
        return [entry for entry in entries if not prefix or entry.path == prefix or entry.path.startswith(f"{prefix}/")]

    def cat_file(self, repository: Any, *, ref: str, path: str) -> bytes:
        """Return the configured blob bytes for ``path`` or raise."""

        del repository, ref
        blobs = self.bridge.config.get("stub_blobs", {})
        if path in blobs:
            return str(blobs[path]).encode("utf-8")
        raise FileNotFoundError(path)

    def rev_parse(self, repository: Any, ref: str) -> str:
        """Return a fixed stub commit oid."""

        del repository, ref
        return "stubsha"

    def verify_webhook(self, vcs_bridge: Any, request: Any) -> bool:
        """Accept every webhook in tests."""

        del vcs_bridge, request
        return True


class StubInferenceBackend(InferenceBackend):
    """In-memory inference backend for tests; canned models ride on ``provider.config``.

    Registered as the ``stub_inference`` key in the test ``ANGEE_INFERENCE_BACKEND_CLASSES`` so
    an ``InferenceProvider(backend_class="stub_inference")`` resolves to it. Each test injects
    ``stub_models`` (a list of ``InferenceModelSpec`` kwargs) through the provider config.
    """

    def list_models(self) -> list[InferenceModelSpec]:
        """Return the models configured on the provider's ``config``."""

        return [InferenceModelSpec(**spec) for spec in self.provider.config.get("stub_models", [])]


class Link(AbstractLink):
    """Concrete knowledge wikilink edge used by source-addon tests."""

    class Meta(AbstractLink.Meta):
        """Django model options for the canonical test link."""

        abstract = False
        app_label = "knowledge"
        db_table = "test_knowledge_link"
        rebac_resource_type = "knowledge/link"
        rebac_id_attr = "sqid"


KNOWLEDGE_TEST_MODELS = (Vault, Page, MarkdownPage, Link)
"""Concrete knowledge models created on demand by knowledge test fixtures."""


class Backend(AbstractStorageBackend):
    """Concrete storage backend used by source-addon tests."""

    class Meta(AbstractStorageBackend.Meta):
        """Django model options for the canonical test storage backend."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_backend"
        rebac_resource_type = "storage/backend"
        rebac_id_attr = "sqid"


class Drive(AbstractDrive):
    """Concrete storage drive used by source-addon tests."""

    class Meta(AbstractDrive.Meta):
        """Django model options for the canonical test drive."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_drive"
        rebac_resource_type = "storage/drive"
        rebac_id_attr = "sqid"


class Folder(AbstractFolder):
    """Concrete storage folder used by source-addon tests."""

    class Meta(AbstractFolder.Meta):
        """Django model options for the canonical test folder."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_folder"
        rebac_resource_type = "storage/folder"
        rebac_id_attr = "sqid"


class MimeType(AbstractMimeType):
    """Concrete MIME type used by source-addon tests."""

    class Meta(AbstractMimeType.Meta):
        """Django model options for the canonical test MIME type."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_mimetype"


class File(AbstractFile):
    """Concrete storage file used by source-addon tests."""

    class Meta(AbstractFile.Meta):
        """Django model options for the canonical test file."""

        abstract = False
        app_label = "storage"
        db_table = "test_storage_file"
        rebac_resource_type = "storage/file"
        rebac_id_attr = "sqid"


STORAGE_TEST_MODELS = (Backend, Drive, Folder, MimeType, File)
"""Concrete storage models created on demand by storage test fixtures."""


class Addon(AbstractAddon, AbstractCatalogProvenance):
    """Concrete platform reflection row used by source-addon tests.

    Folds the ``platform_integrate_vcs`` provenance extension (``vcs_source`` /
    ``vcs_path``) onto the one table, the way the composer folds ``CatalogProvenance``
    onto the emitted ``platform.Addon`` — so the marketplace tier reads its fields off
    one concrete row.
    """

    class Meta(AbstractAddon.Meta):
        """Django model options for the canonical test platform addon row."""

        abstract = False
        app_label = "platform"
        db_table = "test_platform_addon"
        rebac_resource_type = "platform/addon"
        rebac_id_attr = "name"


class PlatformExplorer(AbstractPlatformExplorer):
    """Concrete table-less REBAC anchor for the platform explorer surface."""

    class Meta(AbstractPlatformExplorer.Meta):
        """Django model options for the canonical test platform explorer anchor."""

        abstract = False
        managed = False
        app_label = "platform"
        rebac_resource_type = "platform/explorer"


class AddonCatalog(AbstractAddonCatalog):
    """Concrete addon-source dispatch binding for source-addon tests.

    Table-less (``managed = False``): its ``source_kind`` registers the ``addon`` kind
    with integrate's ``Source`` dispatch, and its manager reconciles discovered rows
    into the ``platform.Addon`` table above.
    """

    source_kind = "addon"

    class Meta(AbstractAddonCatalog.Meta):
        """Django model options for the canonical test addon catalog binding."""

        abstract = False
        managed = False
        app_label = "platform_integrate_vcs"
        rebac_resource_type = "platform_integrate_vcs/catalog"


PLATFORM_TEST_MODELS = (Addon,)
"""Concrete platform reflection table created on demand by marketplace test fixtures."""


def _create_missing_tables(
    test_models: tuple[type[models.Model], ...] = IAM_CONNECTION_TEST_MODELS,
) -> list[type[models.Model]]:
    """Create concrete source-addon test tables when pytest did not sync them."""

    existing_tables = set(connection.introspection.table_names())
    missing = [model for model in test_models if model._meta.db_table not in existing_tables]
    if not missing:
        return []
    with connection.schema_editor() as schema_editor:
        for model in missing:
            schema_editor.create_model(model)
    return missing


@pytest.fixture()
def knowledge_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete knowledge tables and sync the REBAC schema."""

    del transactional_db
    created_models = _create_missing_tables(KNOWLEDGE_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def create_user(username: str) -> Any:
    """Create one plain test user."""

    return get_user_model().objects.create_user(username=username, password=username)


def vault_for(owner: Any, *, name: str = "Research") -> Any:
    """Create a vault owned by ``owner`` through the actor-scoped factory."""

    with actor_context(owner):
        return Vault.objects.create_for(owner, name=name)


_TEST_ADDON_SEQ = itertools.count()


def make_addon(
    *,
    schemas: dict[str, Any] | None = None,
    depends_on: tuple[str, ...] = (),
    name: str | None = None,
) -> AppConfig:
    """Return a fake AppConfig backed by a real tmp ``addon.toml`` (+ schema module).

    Bridges the old in-memory test idiom to the addon.toml contract: ``schemas`` is
    exposed through a registered ``<name>.schema`` module that the manifest's
    ``schemas = "schema.schemas"`` reference resolves to, and ``depends_on`` is written
    straight into the manifest. So the readers (the manifest is their sole source)
    see exactly what the test declares.
    """

    name = name or f"tests._addon_{next(_TEST_ADDON_SEQ)}"
    tmp = Path(tempfile.mkdtemp())
    module = ModuleType(name)
    module.__file__ = str(tmp / "apps.py")
    module.__path__ = [str(tmp)]  # type: ignore[attr-defined]
    sys.modules[name] = module

    body = ["[addon]", f'name = "{name}"']
    if depends_on:
        body.append("depends_on = [" + ", ".join(f'"{dep}"' for dep in depends_on) + "]")
    if schemas is not None:
        schema_module = ModuleType(f"{name}.schema")
        schema_module.schemas = schemas  # type: ignore[attr-defined]
        sys.modules[f"{name}.schema"] = schema_module
        body.append('schemas = "schema.schemas"')
    (tmp / "addon.toml").write_text("\n".join(body) + "\n")

    return AppConfig(name, module)


def SchemaAddon(schemas: dict[str, dict[str, tuple[object, ...]]]) -> AppConfig:  # noqa: N802 - kept for call sites
    """Build an addon stand-in whose manifest exposes the given GraphQL schemas."""

    return make_addon(schemas=schemas)


def make_contract(**overrides: object) -> AddonContract:
    """Build an AddonContract for fake addons, defaulting every unset seam to empty.

    Attached to a stub app config as ``_addon_contract`` and surfaced by the
    test-side contract-reader stub (see ``stub_contracts`` in ``test_compose``), so a
    fake config with no ``addon.toml`` on disk still resolves a declared contract.
    """

    fields: dict[str, object] = {
        "name": "tests.addon",
        "depends_on": (),
        "schemas": None,
        "web": None,
        "web_codegen": None,
        "mcp_tools": None,
        "resources": {},
    }
    fields.update(overrides)
    return AddonContract(**fields)  # type: ignore[arg-type]


def addon_schema(schemas: dict[str, Any], name: str) -> Any:
    """Build one addon-only GraphQL schema from its raw ``schemas`` mapping."""

    parts = {key: tuple(schemas[name].get(key, ())) for key in SCHEMA_PART_KEYS}
    return GraphQLSchemas([SchemaAddon({name: parts})]).build(name)


def graphql_request(user: Any) -> Any:
    """Return a bare POST request carrying ``user``."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    return request


def execute_schema(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a request-shaped context."""

    request = request or graphql_request(user or AnonymousUser())
    actor = getattr(request, "user", AnonymousUser())
    with actor_context(actor):
        return schema.execute_sync(
            query,
            variable_values=variables or {},
            context_value=SimpleNamespace(request=request),
        )


def result_data(result: Any) -> dict[str, Any]:
    """Return result data after asserting the operation succeeded."""

    assert result.errors is None, result.errors
    assert result.data is not None
    return cast(dict[str, Any], result.data)
