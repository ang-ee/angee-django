"""A web form declared as one resource row: the form, the version it serves and its addresses.

The declaring addon ships its vendor, its webform channel and the form, linked by
``depends_on``, as the web forms README shows; the form names the channel by its
xref. The loader owns the form's identity (the ledger xref), its hash and its
transaction, and the form's own verbs serve the version and add the addresses.
Staff edits run as a platform admin through the models' own saves and verbs;
what code made before a declaration existed runs as system work. Every name and
address is invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.messaging.testing.models import Channel
from angee.provenance.context import changeset_model_label, current_changeset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from angee.webforms.models import FormStatus, FormUnavailable
from angee.webforms.testing.models import Form, FormAlias, FormVersion, Response
from tests.conftest import Vendor, create_platform_admin, create_user, make_addon

pytestmark = pytest.mark.django_db

VENDOR = "master/010_integrate.vendor.yaml"
CHANNELS = "master/020_messaging.channel.yaml"
FORM = "install/030_webforms.form.yaml"
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


def declared(**changes: Any) -> dict[str, Any]:
    """The declared form's fields, with ``changes`` over them."""

    return {
        "name": "Intake",
        "target_class": "message",
        "target_config": {"channel": "webforms_seed.intake", "slug": "intake"},
        "aliases": ["intake"],
        "version": VERSION,
        **changes,
    }


class Declaring:
    """The declaring addon: its vendor and channels in master, its form in install, rewritten before each load."""

    def __init__(self, path: Path, *, create_only: tuple[str, ...] = ("retention_days",)) -> None:
        self.root = path / "resources"
        self.write(VENDOR, [{"xref": "vendor", "fields": {"slug": "webform", "display_name": "Public webforms"}}])
        self.write(CHANNELS, [
            {"xref": "intake", "fields": {
                "display_name": "Intake", "vendor": "webforms_seed.vendor", "backend_class": "webform",
            }},
            {"xref": "mailbox", "fields": {
                "display_name": "Mailbox", "vendor": "webforms_seed.vendor", "backend_class": "manual",
            }},
        ])
        form: dict[str, Any] = {"path": FORM, "depends_on": CHANNELS}
        if create_only:
            form["create_only"] = list(create_only)
        self.addon = make_addon(name="tests.webforms_seed", label="webforms_seed", path=path, resources={
            "master": [{"path": VENDOR, "adopt": "slug"}, {"path": CHANNELS, "depends_on": VENDOR}],
            "install": [form],
        })

    def write(self, name: str, rows: list[dict[str, Any]]) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump({"rows": rows}, sort_keys=False))

    def declare(self, **changes: Any) -> None:
        """Rewrite the form's row with ``changes`` over the declared fields."""

        self.write(FORM, [{"xref": "intake_form", "fields": declared(**changes)}])

    def load(self, **changes: Any) -> Any:
        """Declare the form with ``changes`` over it and load the addon's resources."""

        self.declare(**changes)
        return Resource.objects.load_addons((self.addon,), tiers=[Resource.Tier.INSTALL])

    def load_channels(self) -> None:
        Resource.objects.load_addons((self.addon,), tiers=[Resource.Tier.MASTER])

    def channel(self, xref: str = "intake") -> Any:
        with system_context(reason="webforms resources tests read"):
            target = Resource.objects.get(source_addon=self.addon.name, xref=xref).target_instance()
        assert isinstance(target, Channel)
        return target

    def form(self) -> Any:
        """The one form there is, read through the ledger."""

        with system_context(reason="webforms resources tests read"):
            assert Form._base_manager.count() == 1
            target = Resource.objects.get(source_addon=self.addon.name, xref="intake_form").target_instance()
            assert isinstance(target, Form)
            return target


@pytest.fixture
def declaring(tmp_path: Path) -> Declaring:
    return Declaring(tmp_path)


@pytest.fixture
def staff() -> Any:
    """A platform admin, who manages forms and may write on every channel."""

    return create_platform_admin("webforms-staff")


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


def addresses(form: Any) -> dict[str, str]:
    """Each alias of ``form``: its slug and the language it opens the form in."""

    with system_context(reason="webforms resources tests read"):
        return dict(FormAlias._base_manager.filter(form=form).values_list("slug", "language"))


def history(form: Any) -> int:
    with system_context(reason="webforms resources tests read"):
        return form.history.count()


def nothing_made(declaring: Declaring) -> None:
    with system_context(reason="webforms resources tests read"):
        assert not Form._base_manager.exists()
        assert not FormVersion._base_manager.exists()
        assert not FormAlias._base_manager.exists()
        assert not Resource.objects.filter(source_addon=declaring.addon.name, xref="intake_form").exists()


def test_a_load_makes_one_form_serving_its_published_version_at_its_address(declaring: Declaring) -> None:
    result = declaring.load()

    form = declaring.form()
    # The vendor, two channels and the form.
    assert (result.created, result.updated, result.skipped) == (4, 0, 0)
    assert (form.name, form.status, str(form.target_class)) == ("Intake", FormStatus.PUBLISHED, "message")
    # The channel's xref became its public id: what the target reads, and what one conversation per channel checks.
    assert form.target_config == {"channel": declaring.channel().sqid, "slug": "intake"}
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

    assert (result.created, result.updated, result.skipped) == (0, 0, 4)
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
    declaring: Declaring, staff: Any, edit: str,
) -> None:
    declaring.load()
    form = declaring.form()
    with actor_context(staff):
        alias = FormAlias.objects.get(form=form)
        if edit == "rename":
            alias.slug = "intake-desk"
            alias.save()
        else:
            alias.delete()
    left = {"intake-desk"} if edit == "rename" else set()

    assert declaring.load().skipped == 4
    assert declaring.form().pk == form.pk
    assert slugs(form) == left

    # A changed declaration answers at its addresses again, beside the one staff chose.
    assert declaring.load(name="Intake form").updated == 1
    assert declaring.form().pk == form.pk
    assert slugs(form) == {"intake", *left}


def test_staff_closing_the_form_survives_reloads_and_a_changed_declaration_serves_it_closed(
    declaring: Declaring, staff: Any,
) -> None:
    declaring.load()
    with actor_context(staff):
        Form.objects.get(pk=declaring.form().pk).close()

    assert declaring.load().skipped == 4
    assert declaring.form().status == FormStatus.CLOSED

    declaring.load(version={**VERSION, "definition": LONGER})
    form = declaring.form()
    assert (form.status, form.published_version.number) == (FormStatus.CLOSED, 2)


def test_a_version_staff_published_survives_reloads_and_any_changed_row_serves_the_declared_content_again(
    declaring: Declaring, staff: Any,
) -> None:
    declaring.load()
    form = declaring.form()
    with actor_context(staff):
        FormVersion.objects.create(form=form, **{**VERSION, "definition": LONGER}).publish()

    assert declaring.load().skipped == 4
    assert declaring.form().published_version.number == 2

    # A row that changes only the form's name still serves its declared content, over staff's version.
    assert declaring.load(name="Intake form").updated == 1
    assert versions(form) == [(1, DEFINITION, True), (2, LONGER, True), (3, DEFINITION, True)]
    assert declaring.form().published_version.number == 3


def test_an_entry_that_leaves_the_questions_to_staff_lists_version_under_create_only(
    tmp_path: Path, staff: Any,
) -> None:
    declaring = Declaring(tmp_path, create_only=("retention_days", "version"))
    declaring.load()
    form = declaring.form()
    with actor_context(staff):
        FormVersion.objects.create(form=form, **{**VERSION, "definition": LONGER}).publish()

    ignored = {**VERSION, "definition": {**DEFINITION, "title": "Ignored"}}
    assert declaring.load(name="Intake form", version=ignored).updated == 1

    assert (declaring.form().name, declaring.form().published_version.number) == ("Intake form", 2)
    assert versions(form) == [(1, DEFINITION, True), (2, LONGER, True)]


def test_a_draft_declared_form_is_made_and_kept_a_draft_its_declared_content_prepared_once(
    declaring: Declaring,
) -> None:
    """``publish: false``: the load prepares the version and a person publishes it; the form is public only then."""

    assert declaring.load(publish=False).created == 4
    form = declaring.form()
    assert (form.status, form.published_version_id) == (FormStatus.DRAFT, None)
    assert versions(form) == [(1, DEFINITION, False)]
    assert slugs(form) == {"intake"}
    with system_context(reason="webforms resources tests read"):
        assert not FormAlias.objects.serving().exists()
    with pytest.raises(FormUnavailable):
        Response.objects.receive(form, version=1, submission_id="r-1", language="en", answers={"name": "Ada"})

    assert declaring.load(publish=False).skipped == 4
    # A changed row whose content the newest version holds makes nothing; new content is a new draft beside it.
    assert declaring.load(publish=False, name="Intake form").updated == 1
    assert versions(form) == [(1, DEFINITION, False)]
    assert declaring.load(publish=False, version={**VERSION, "definition": LONGER}).updated == 1
    form = declaring.form()
    assert (form.status, form.published_version_id) == (FormStatus.DRAFT, None)
    assert versions(form) == [(1, DEFINITION, False), (2, LONGER, False)]


def test_a_draft_declared_form_staff_published_keeps_their_state_through_reloads_and_changed_rows(
    declaring: Declaring, staff: Any,
) -> None:
    declaring.load(publish=False)
    form = declaring.form()
    with actor_context(staff):
        FormVersion.objects.get(form=form, number=1).publish()
        # Staff go on to prepare questions of their own.
        FormVersion.objects.create(form=form, **{**VERSION, "definition": {**DEFINITION, "title": "Staff's"}})

    assert declaring.load(publish=False).skipped == 4
    # The version staff serve holds the declared content: a changed row makes nothing and publishes nothing.
    assert declaring.load(publish=False, name="Intake form").updated == 1
    form = declaring.form()
    assert (form.status, form.published_version.number, len(versions(form))) == (FormStatus.PUBLISHED, 1, 2)

    # New declared content is prepared beside staff's work; what the public answers stays theirs, closed or not.
    with actor_context(staff):
        Form.objects.get(pk=form.pk).close()
    assert declaring.load(publish=False, version={**VERSION, "definition": LONGER}).updated == 1
    form = declaring.form()
    assert (form.status, form.published_version.number) == (FormStatus.CLOSED, 1)
    assert [(number, published) for number, _definition, published in versions(form)] == [
        (1, True), (2, False), (3, False),
    ]
    assert versions(form)[2][1] == LONGER


def test_a_row_that_turns_publish_on_publishes_the_version_it_prepared(declaring: Declaring) -> None:
    declaring.load(publish=False)

    assert declaring.load(publish=True).updated == 1

    form = declaring.form()
    assert (form.status, form.published_version.number) == (FormStatus.PUBLISHED, 1)
    assert versions(form) == [(1, DEFINITION, True)]


def test_a_row_that_turns_publish_off_leaves_the_form_served_and_prepares_new_content_beside_it(
    declaring: Declaring,
) -> None:
    declaring.load()

    assert declaring.load(publish=False).updated == 1
    assert versions(declaring.form()) == [(1, DEFINITION, True)]
    declaring.load(publish=False, version={**VERSION, "definition": LONGER})

    form = declaring.form()
    assert (form.status, form.published_version.number) == (FormStatus.PUBLISHED, 1)
    assert versions(form) == [(1, DEFINITION, True), (2, LONGER, False)]


def test_a_draft_declared_version_is_judged_for_publication_when_a_person_publishes_it(
    declaring: Declaring, staff: Any,
) -> None:
    declaring.load(publish=False, version={**VERSION, "personal_questions": None})
    form = declaring.form()

    with actor_context(staff), pytest.raises(ValidationError, match="State which questions hold personal data"):
        FormVersion.objects.get(form=form, number=1).publish()

    assert (declaring.form().status, versions(form)) == (FormStatus.DRAFT, [(1, DEFINITION, False)])


@pytest.mark.parametrize(("create_only", "fields", "refusal"), [
    pytest.param(
        ("retention_days", "publish"), {"publish": False}, "publish applies on every changed row",
        id="publish-under-create-only",
    ),
    pytest.param(
        ("retention_days",), {"status": "published", "publish": False}, "a declared form's status follows",
        id="a-declared-status",
    ),
])
def test_a_file_that_would_set_the_forms_state_behind_its_verbs_is_refused(
    tmp_path: Path, create_only: tuple[str, ...], fields: dict[str, Any], refusal: str,
) -> None:
    """Under create_only a changed row would drop publish and publish the form; a status would skip open()."""

    declaring = Declaring(tmp_path, create_only=create_only)

    with pytest.raises(ResourceLoadError, match=refusal):
        declaring.load(**fields)

    nothing_made(declaring)


def test_an_alias_may_name_the_language_its_address_opens_the_form_in_and_staffs_choice_stays(
    declaring: Declaring, staff: Any,
) -> None:
    declared_aliases = ["intake", {"slug": "entrada", "language": "es"}, {"slug": "intake-en", "language": "en"}]
    declaring.load(aliases=declared_aliases)
    form = declaring.form()
    assert addresses(form) == {"intake": "", "entrada": "es", "intake-en": "en"}

    with actor_context(staff):
        alias = FormAlias.objects.get(slug="entrada")
        alias.language = "pt-BR"
        alias.save()
    # A changed row adds what the form lacks and leaves every alias it has as it is.
    changed = declaring.load(name="Intake form", aliases=[*declared_aliases, {"slug": "seed", "language": "en"}])
    assert changed.updated == 1
    assert addresses(form) == {"intake": "", "entrada": "pt-BR", "intake-en": "en", "seed": "en"}


def test_every_alias_the_form_cannot_take_is_reported_under_aliases_naming_it(declaring: Declaring) -> None:
    aliases = [{"slug": "entrada", "language": "Spanish!"}, "Bad Slug", "intake"]

    with pytest.raises(ResourceLoadError) as refused:
        declaring.load(aliases=aliases)

    assert "entrada: Enter a language tag" in str(refused.value)
    assert "Bad Slug: Use lower-case letters and numbers" in str(refused.value)
    nothing_made(declaring)


def test_aliases_given_as_one_slug_rather_than_a_collection_are_refused_whole(staff: Any) -> None:
    with actor_context(staff):
        form = Form.objects.create(name="Staff form")
        with pytest.raises(ValidationError, match="Give a collection of aliases, not one alias"):
            form.ensure_aliases("desk")
        with pytest.raises(ValidationError, match="Give a collection of aliases, not one alias"):
            form.ensure_aliases({"slug": "desk"})

    assert slugs(form) == set()


def test_a_row_without_a_version_is_refused_whether_it_would_make_the_form_or_change_it(
    declaring: Declaring,
) -> None:
    fields = declared()
    del fields["version"]
    declaring.write(FORM, [{"xref": "intake_form", "fields": fields}])

    with pytest.raises(ResourceLoadError, match="Declare version"):
        Resource.objects.load_addons((declaring.addon,), tiers=[Resource.Tier.INSTALL])
    nothing_made(declaring)

    declaring.load()
    declaring.write(FORM, [{"xref": "intake_form", "fields": {**fields, "name": "Renamed"}}])
    with pytest.raises(ResourceLoadError, match="Declare version"):
        Resource.objects.load_addons((declaring.addon,), tiers=[Resource.Tier.INSTALL])
    assert declaring.form().name == "Intake"


def test_a_version_the_publication_refuses_leaves_nothing_behind(declaring: Declaring) -> None:
    with pytest.raises(ResourceLoadError, match="State which questions hold personal data"):
        declaring.load(version={**VERSION, "personal_questions": None})

    nothing_made(declaring)


def test_a_version_field_its_column_cannot_hold_is_a_field_refusal(declaring: Declaring) -> None:
    long = "e" * 65
    content = {**VERSION, "contact_question": long, "personal_questions": ["name", "email", long]}

    with pytest.raises(ResourceLoadError, match=r"contact_question.*at most 64 characters"):
        declaring.load(version=content)

    nothing_made(declaring)


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
    assert declaring.load().skipped == 4


def test_a_channel_reference_is_resolved_among_the_loads_own_addons(declaring: Declaring) -> None:
    """An installed addon outside the load is out of the row's scope, as it is for the row's relation columns."""

    with system_context(reason="webforms resources tests setup"):
        Vendor.objects.get_or_create(slug="webform", defaults={"display_name": "Webform"})
        elsewhere = Channel.objects.create_disconnected(
            create_user("channel-elsewhere"), name="Elsewhere", backend_class="webform",
        )
        Resource.objects.bind_instance(
            addon=apps.get_app_config("webformsdemo"), xref="elsewhere", instance=elsewhere, source="test",
        )

    with pytest.raises(ResourceLoadError, match="target_config.*unresolved xref 'webformsdemo.elsewhere'"):
        declaring.load(target_config={"channel": "webformsdemo.elsewhere", "slug": "intake"})

    nothing_made(declaring)


@pytest.mark.parametrize(("reference", "refusal"), [
    pytest.param("webforms_seed.missing", "unresolved xref 'webforms_seed.missing'", id="an-unknown-xref"),
    pytest.param("webforms_seed.vendor", "targets .*, not messaging.Channel", id="an-xref-to-another-kind-of-record"),
    pytest.param("webforms_seed.mailbox", "No Public Webform channel", id="a-channel-of-another-backend"),
])
def test_a_channel_reference_that_names_no_webform_channel_is_refused_and_makes_nothing(
    declaring: Declaring, reference: str, refusal: str,
) -> None:
    with pytest.raises(ResourceLoadError, match=rf"target_config.*{refusal}") as refused:
        declaring.load(target_config={"channel": reference, "slug": "intake"})

    # A reference that did not resolve is reported once, not judged again as a channel id.
    assert ("No Public Webform channel" in str(refused.value)) is (reference == "webforms_seed.mailbox")
    nothing_made(declaring)


def test_a_channels_public_id_is_kept_as_it_is_declared(declaring: Declaring) -> None:
    declaring.load_channels()
    public_id = declaring.channel().sqid

    declaring.load(target_config={"channel": public_id, "slug": "intake"})

    assert declaring.form().target_config == {"channel": public_id, "slug": "intake"}


def test_a_declared_retention_must_be_create_only(tmp_path: Path) -> None:
    declaring = Declaring(tmp_path, create_only=())

    with pytest.raises(ResourceLoadError, match="retention_days is the deployment's once a form exists"):
        declaring.load(retention_days=30)

    nothing_made(declaring)


def test_a_create_only_retention_reaches_the_new_form_and_never_the_deployments_own(
    declaring: Declaring, staff: Any,
) -> None:
    declaring.load(retention_days=30)
    assert declaring.form().retention_days == 30
    with actor_context(staff):
        edited = Form.objects.get(pk=declaring.form().pk)
        edited.retention_days = 365
        edited.save(update_fields=["retention_days", "updated_at"])

    assert declaring.load(name="Intake form", retention_days=7).updated == 1
    assert (declaring.form().name, declaring.form().retention_days) == ("Intake form", 365)


@pytest.mark.parametrize(("changes", "refusal"), [
    pytest.param({"version": {**VERSION, "number": 4}}, "it does not hold number", id="a-field-no-version-holds"),
    pytest.param({"version": ["not", "a", "mapping"]}, "Declare version as a mapping", id="a-version-no-mapping"),
    pytest.param({"publish": "maybe"}, "Declare publish as true or false", id="a-publish-that-is-a-string"),
    pytest.param({"aliases": "intake"}, "Declare aliases as a list", id="aliases-that-are-no-list"),
    pytest.param({"aliases": ["Intake Desk"]}, "Use lower-case letters and numbers", id="an-alias-that-is-no-slug"),
    pytest.param(
        {"aliases": [{"slug": "entrada", "lang": "es"}]}, "Give an alias as a slug, or as a mapping of slug, language",
        id="an-alias-with-a-key-of-its-own",
    ),
    pytest.param(
        {"aliases": [{"language": "es"}]}, "Give an alias as a slug, or as a mapping of slug, language",
        id="an-alias-without-a-slug",
    ),
    pytest.param(
        {"aliases": ["intake", {"slug": "intake", "language": "es"}]}, "intake: given twice",
        id="an-alias-declared-twice",
    ),
    pytest.param(
        {"aliases": ["intake", {"slug": "entrada", "language": "Spanish!"}]}, "entrada: Enter a language tag",
        id="an-alias-language-that-is-no-tag",
    ),
    pytest.param(
        {"aliases": [{"slug": "entrada", "language": None}]}, "entrada: Give an alias as a slug",
        id="an-alias-language-left-empty",
    ),
    pytest.param({"aliases": ["taken"]}, "The address forms/taken belongs to another form", id="another-forms-alias"),
])
def test_a_declared_version_or_alias_the_form_cannot_take_is_refused_and_makes_nothing(
    declaring: Declaring, staff: Any, changes: dict[str, Any], refusal: str,
) -> None:
    with actor_context(staff):
        other = Form.objects.create(name="Staff form")
        FormAlias.objects.create(form=other, slug="taken")

    with pytest.raises(ResourceLoadError, match=refusal):
        declaring.load(**changes)

    with system_context(reason="webforms resources tests read"):
        assert list(Form._base_manager.values_list("name", flat=True)) == ["Staff form"]
        assert list(FormAlias._base_manager.values_list("slug", flat=True)) == ["taken"]


def test_a_form_made_before_the_declaration_is_adopted_by_binding_it_to_the_rows_xref(
    declaring: Declaring,
) -> None:
    """A form the move template or a consumer's own code made already posts on the channel under the slug."""

    declaring.load_channels()
    with system_context(reason="webforms resources tests: a form made before the declaration"):
        earlier = Form.objects.create(
            name="Painless intake", target_class="message", retention_days=90,
            target_config={"channel": declaring.channel().sqid, "slug": "intake"},
        )
        FormVersion.objects.create(form=earlier, **{**VERSION, "definition": LONGER}).publish()
        FormAlias.objects.create(form=earlier, slug="intake")
        FormAlias.objects.create(form=earlier, slug="painless")

    # One channel and slug take one form: the declared one is refused while the earlier one holds them.
    with pytest.raises(ResourceLoadError, match="Another form already posts on this channel under this slug"):
        declaring.load(retention_days=30)

    # The source is the one the loader keeps for the entry: its manifest path under the ledger's ``resources/``.
    source = f"resources/{FORM}"
    with system_context(reason="webforms resources tests: adopt the earlier form"):
        Resource.objects.bind_instance(addon=declaring.addon, xref="intake_form", instance=earlier, source=source)
    assert declaring.load(retention_days=30).updated == 1

    form = declaring.form()
    assert form.pk == earlier.pk
    with system_context(reason="webforms resources tests read"):
        assert Resource.objects.get(source_addon=declaring.addon.name, xref="intake_form").source_path == source
    # Declared fields apply, the retention stays the deployment's, the declared content is served, the aliases stay.
    assert (form.name, form.retention_days, form.published_version.number) == ("Intake", 90, 2)
    assert versions(form) == [(1, LONGER, True), (2, DEFINITION, True)]
    assert slugs(form) == {"intake", "painless"}
    assert declaring.load(retention_days=30).skipped == 4


def test_a_row_loaded_for_a_named_actor_is_saved_as_that_actor(
    declaring: Declaring, staff: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managing forms is not posting on the channel: ``load_xref``'s actor is the one the target asks."""

    manager = create_user("webforms-only-manager")
    with system_context(reason="webforms resources tests setup"):
        grant_role(actor=manager, role="webforms/role:webforms_admin")
    declaring.load_channels()
    declaring.declare()
    addon = declaring.addon
    addon.apps = apps
    addon.models = {}
    handle = f"{addon.name}.intake_form"
    try:
        monkeypatch.setitem(apps.app_configs, addon.label, addon)
        apps.clear_cache()

        with pytest.raises(ResourceLoadError, match="may not point a form at 'message'"):
            Resource.objects.load_xref(handle, model=Form, actor=manager)
        nothing_made(declaring)

        form = Resource.objects.load_xref(handle, model=Form, actor=staff)
    finally:
        monkeypatch.delitem(apps.app_configs, addon.label)
        apps.clear_cache()

    assert form.pk == declaring.form().pk
    assert versions(form) == [(1, DEFINITION, True)]


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
