"""A web form declared as one resource row: the form, the version it serves and its addresses.

An addon's resource file declares the form; the loader owns its identity (the
ledger xref), its hash and its transaction, and the form's own verbs serve the
version and add the addresses. The form posts on a webform channel the declaring
addon knows by xref, as a consumer's intake form does. Staff edits are made under
``system_context``, as the console's writes would land. Every name and address
is invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from rebac import system_context

from angee.messaging.testing.models import Channel
from angee.provenance.context import changeset_model_label, current_changeset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from angee.webforms.models import FormStatus
from angee.webforms.testing.models import Form, FormAlias, FormVersion, Response
from tests.conftest import Vendor, create_user, make_addon

pytestmark = pytest.mark.django_db

SOURCE = "030_webforms.form.yaml"
DEFINITION: dict[str, Any] = {
    "title": "Tell us",
    "elements": [
        {"type": "text", "name": "name", "isRequired": True, "title": "Your name"},
        {"type": "text", "name": "email", "inputType": "email"},
    ],
}
LONGER: dict[str, Any] = {
    **DEFINITION, "elements": [*DEFINITION["elements"], {"type": "comment", "name": "details"}],
}
VERSION: dict[str, Any] = {
    "definition": DEFINITION, "languages": ["en"], "default_language": "en",
    "personal_questions": ["name", "email"], "contact_question": "email",
}


@pytest.fixture
def intake() -> Any:
    """A webform channel, ledgered as ``webformsdemo.intake`` the way a declaring addon's resources ledger it."""

    with system_context(reason="webforms resources tests setup"):
        Vendor.objects.get_or_create(slug="webform", defaults={"display_name": "Webform"})
        owner = create_user("intake-owner")
        channel = Channel.objects.create_disconnected(owner, name="Intake", backend_class="webform")
        Resource.objects.bind_instance(
            addon=apps.get_app_config("webformsdemo"), xref="intake", instance=channel, source="test",
        )
    return channel


def declared(**changes: Any) -> dict[str, Any]:
    """The declared form's fields, with ``changes`` over them."""

    return {
        "name": "Intake",
        "target_class": "message",
        "target_config": {"channel": "webformsdemo.intake", "slug": "intake"},
        "aliases": ["intake"],
        "version": VERSION,
        **changes,
    }


class Declaring:
    """A declaring addon whose one install entry is the form's file, rewritten before each load."""

    def __init__(self, path: Path, *, create_only: tuple[str, ...] = ()) -> None:
        (path / "resources").mkdir(exist_ok=True)
        self.file = path / "resources" / SOURCE
        entry: dict[str, Any] = {"path": SOURCE}
        if create_only:
            entry["create_only"] = list(create_only)
        self.addon = make_addon(name="tests.webforms_seed", label="webforms_seed", path=path, resources={
            "install": [entry],
        })

    def load(self, **changes: Any) -> Any:
        """Declare the form with ``changes`` over it and load the addon's resources."""

        row = {"xref": "intake_form", "fields": declared(**changes)}
        self.file.write_text(yaml.safe_dump({"rows": [row]}, sort_keys=False))
        return Resource.objects.load_addons((self.addon,), tiers=[Resource.Tier.INSTALL])

    def form(self) -> Any:
        """The one form the declaration made, read through the ledger."""

        with system_context(reason="webforms resources tests read"):
            assert Form._base_manager.count() == 1
            target = Resource.objects.get(source_addon=self.addon.name, xref="intake_form").target_instance()
            assert isinstance(target, Form)
            return target


@pytest.fixture
def declaring(tmp_path: Path, intake: Any) -> Declaring:
    del intake
    return Declaring(tmp_path)


def versions(form: Any) -> list[tuple[int, dict[str, Any], bool]]:
    """Each version of ``form``: its number, its definition, and whether it is published."""

    with system_context(reason="webforms resources tests read"):
        return [
            (version.number, version.definition, version.published_at is not None)
            for version in FormVersion._base_manager.filter(form=form).order_by("number")
        ]


def slugs(form: Any) -> set[str]:
    with system_context(reason="webforms resources tests read"):
        return set(FormAlias._base_manager.filter(form=form).values_list("slug", flat=True))


def history(form: Any) -> int:
    with system_context(reason="webforms resources tests read"):
        return form.history.count()


def test_a_load_makes_one_form_serving_its_published_version_at_its_address(declaring: Declaring, intake: Any) -> None:
    result = declaring.load()

    form = declaring.form()
    assert (result.created, result.updated, result.skipped) == (1, 0, 0)
    assert (form.name, form.status, str(form.target_class)) == ("Intake", FormStatus.PUBLISHED, "message")
    # The channel's xref became its public id: what the target reads, and what one conversation per channel checks.
    assert form.target_config == {"channel": intake.sqid, "slug": "intake"}
    with system_context(reason="webforms resources tests read"):
        served = FormVersion._base_manager.get(pk=form.published_version_id)
        assert FormAlias.objects.serving().get(slug="intake").form_id == form.pk
    assert (served.number, served.default_language, served.contact_question) == (1, "en", "email")
    assert (served.definition, served.personal_questions) == (DEFINITION, ["name", "email"])
    assert versions(form) == [(1, DEFINITION, True)]
    # The declared form takes a submission at once.
    response, made = Response.objects.receive(form, version=1, submission_id="r-1", language="en", answers={
        "name": "Ada Example",
    })
    assert made and response.form_id == form.pk


def test_an_unchanged_reload_writes_nothing(declaring: Declaring) -> None:
    declaring.load()
    form = declaring.form()
    written = history(form)
    with system_context(reason="webforms resources tests read"):
        ledger = Resource.objects.get(source_addon=declaring.addon.name, xref="intake_form")

    result = declaring.load()

    assert (result.created, result.updated, result.skipped) == (0, 0, 1)
    assert history(declaring.form()) == written
    assert versions(form) == [(1, DEFINITION, True)]
    assert slugs(form) == {"intake"}
    with system_context(reason="webforms resources tests read"):
        assert Resource.objects.get(pk=ledger.pk).content_hash == ledger.content_hash


def test_a_changed_declaration_serves_new_content_as_a_new_version_and_a_return_to_earlier_content_too(
    declaring: Declaring,
) -> None:
    declaring.load()

    assert declaring.load(version={**VERSION, "definition": LONGER}).updated == 1
    form = declaring.form()
    assert versions(form) == [(1, DEFINITION, True), (2, LONGER, True)]
    assert form.published_version.number == 2

    # A change that leaves the content alone applies the form's fields and serves the version it serves.
    declaring.load(name="Intake form", version={**VERSION, "definition": LONGER})
    form = declaring.form()
    assert (form.name, form.published_version.number) == ("Intake form", 2)
    assert len(versions(form)) == 2

    # The earlier content is a change of the row: it is served again, as a new version.
    assert declaring.load(name="Intake form").updated == 1
    form = declaring.form()
    assert versions(form) == [(1, DEFINITION, True), (2, LONGER, True), (3, DEFINITION, True)]
    assert form.published_version.number == 3


@pytest.mark.parametrize("edit", ["rename", "delete"])
def test_staff_renaming_or_deleting_the_alias_neither_fails_a_reload_nor_makes_a_second_form(
    declaring: Declaring, edit: str,
) -> None:
    declaring.load()
    form = declaring.form()
    with system_context(reason="webforms resources tests staff edit"):
        alias = FormAlias._base_manager.get(form=form)
        if edit == "rename":
            alias.slug = "intake-desk"
            alias.save()
        else:
            alias.delete()
    left = {"intake-desk"} if edit == "rename" else set()

    assert declaring.load().skipped == 1
    assert declaring.form().pk == form.pk
    assert slugs(form) == left

    # A changed declaration answers at its addresses again, beside the one staff chose.
    assert declaring.load(name="Intake form").updated == 1
    assert declaring.form().pk == form.pk
    assert slugs(form) == {"intake", *left}


def test_staff_closing_the_form_survives_reloads_and_a_changed_declaration_serves_it_closed(
    declaring: Declaring,
) -> None:
    declaring.load()
    with system_context(reason="webforms resources tests staff edit"):
        declaring.form().close()

    assert declaring.load().skipped == 1
    assert declaring.form().status == FormStatus.CLOSED

    declaring.load(version={**VERSION, "definition": LONGER})
    form = declaring.form()
    assert (form.status, form.published_version.number) == (FormStatus.CLOSED, 2)


def test_a_version_staff_published_survives_reloads_until_the_declaration_changes(declaring: Declaring) -> None:
    declaring.load()
    form = declaring.form()
    with system_context(reason="webforms resources tests staff edit"):
        FormVersion.objects.create(form=form, **{**VERSION, "definition": LONGER}).publish()

    assert declaring.load().skipped == 1
    assert declaring.form().published_version.number == 2

    # The declaration's own content, now changed, is what a changed row serves.
    changed = {**DEFINITION, "title": "Tell us more"}
    declaring.load(version={**VERSION, "definition": changed})
    assert versions(form) == [(1, DEFINITION, True), (2, LONGER, True), (3, changed, True)]
    assert declaring.form().published_version.number == 3


def test_a_version_the_publication_refuses_leaves_nothing_behind(tmp_path: Path, intake: Any) -> None:
    del intake
    declaring = Declaring(tmp_path)

    with pytest.raises(ResourceLoadError, match="State which questions hold personal data"):
        declaring.load(version={**VERSION, "personal_questions": None})

    with system_context(reason="webforms resources tests read"):
        assert not Form._base_manager.exists()
        assert not FormVersion._base_manager.exists()
        assert not FormAlias._base_manager.exists()
        assert not Resource.objects.filter(source_addon=declaring.addon.name).exists()


def test_a_changed_declaration_the_publication_refuses_leaves_the_served_version_and_the_ledger(
    declaring: Declaring,
) -> None:
    declaring.load()
    form = declaring.form()
    asked = {**DEFINITION, "elements": [*DEFINITION["elements"], {"type": "text", "name": "version"}]}

    with pytest.raises(ResourceLoadError, match="the name 'version' is the submission's own"):
        declaring.load(name="Renamed", aliases=["intake", "desk"], version={**VERSION, "definition": asked})

    assert (declaring.form().name, declaring.form().published_version.number) == ("Intake", 1)
    assert versions(form) == [(1, DEFINITION, True)]
    assert slugs(form) == {"intake"}
    # The ledger kept the declaration that applied, so the next load tries the change again.
    assert declaring.load().skipped == 1


@pytest.mark.parametrize(("reference", "refusal"), [
    pytest.param("webformsdemo.missing", "unresolved xref 'webformsdemo.missing'", id="an-unknown-xref"),
    pytest.param("webformsdemo.owner", "targets .*, not messaging.Channel", id="an-xref-to-another-kind-of-record"),
])
def test_a_channel_reference_that_names_no_channel_is_refused_and_makes_nothing(
    declaring: Declaring, reference: str, refusal: str,
) -> None:
    with system_context(reason="webforms resources tests setup"):
        Resource.objects.bind_instance(
            addon=apps.get_app_config("webformsdemo"), xref="owner", instance=create_user("not-a-channel"),
            source="test",
        )

    with pytest.raises(ResourceLoadError, match=rf"target_config.*{refusal}"):
        declaring.load(target_config={"channel": reference, "slug": "intake"})

    with system_context(reason="webforms resources tests read"):
        assert not Form._base_manager.exists()


def test_a_channels_public_id_is_kept_as_it_is_declared(declaring: Declaring, intake: Any) -> None:
    declaring.load(target_config={"channel": intake.sqid, "slug": "intake"})

    assert declaring.form().target_config == {"channel": intake.sqid, "slug": "intake"}


def test_a_declared_retention_must_be_create_only(declaring: Declaring) -> None:
    with pytest.raises(ResourceLoadError, match="retention_days is the deployment's once a form exists"):
        declaring.load(retention_days=30)

    with system_context(reason="webforms resources tests read"):
        assert not Form._base_manager.exists()


def test_a_create_only_retention_reaches_the_new_form_and_never_the_deployments_own(
    tmp_path: Path, intake: Any,
) -> None:
    del intake
    declaring = Declaring(tmp_path, create_only=("retention_days",))

    declaring.load(retention_days=30)
    assert declaring.form().retention_days == 30
    with system_context(reason="webforms resources tests staff edit"):
        Form._base_manager.filter(pk=declaring.form().pk).update(retention_days=365)

    assert declaring.load(name="Intake form", retention_days=7).updated == 1
    assert (declaring.form().name, declaring.form().retention_days) == ("Intake form", 365)


@pytest.mark.parametrize(("changes", "refusal"), [
    pytest.param({"version": {**VERSION, "number": 4}}, "it does not hold number", id="a-field-no-version-holds"),
    pytest.param({"version": ["not", "a", "mapping"]}, "Declare version as a mapping", id="a-version-no-mapping"),
    pytest.param({"aliases": "intake"}, "Declare aliases as a list of slugs", id="aliases-that-are-no-list"),
    pytest.param({"aliases": ["Intake Desk"]}, "Use lower-case letters and numbers", id="an-alias-that-is-no-slug"),
    pytest.param({"aliases": ["taken"]}, "The address forms/taken belongs to another form", id="another-forms-alias"),
])
def test_a_declared_version_or_alias_the_form_cannot_take_is_refused_and_makes_nothing(
    declaring: Declaring, changes: dict[str, Any], refusal: str,
) -> None:
    with system_context(reason="webforms resources tests setup"):
        other = Form.objects.create(name="Staff form")
        FormAlias.objects.create(form=other, slug="taken")

    with pytest.raises(ResourceLoadError, match=refusal):
        declaring.load(**changes)

    with system_context(reason="webforms resources tests read"):
        assert list(Form._base_manager.values_list("name", flat=True)) == ["Staff form"]
        assert list(FormAlias._base_manager.values_list("slug", flat=True)) == ["taken"]


@pytest.mark.entrypoint_boundaries
def test_the_loaders_writes_are_attributed_to_its_own_seed_changeset(declaring: Declaring) -> None:
    """No ambient test changeset: under strict mode a form saved outside the loader's boundary would be refused."""

    assert current_changeset() is None
    declaring.load()
    form = declaring.form()
    with system_context(reason="webforms resources tests read"):
        rows = list(form.history.order_by("history_id"))
        changesets = {row.changeset_id for row in rows}
        (changeset,) = (row.changeset for row in rows[:1])
    # The form's insert, its publication's write and its opening: each attributed to the one seed load.
    assert len(rows) >= 2 and changesets == {changeset.pk}
    assert (changeset.activity, changeset.change_reason) == ("seed", "resources.load")

    declaring.load(name="Intake form")
    with system_context(reason="webforms resources tests read"):
        later = {row.changeset_id for row in form.history.order_by("history_id")[len(rows):]}
        (again,) = later
        assert again != changeset.pk
        assert apps.get_model(changeset_model_label())._base_manager.get(pk=again).activity == "seed"
