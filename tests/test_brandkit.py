"""Brand kits: theme-named colours, the font-stack field, one default kit, the mail palette and who may do what.

Kits are deployment configuration: setup rows are written under ``system_context``
and console writes run as a platform admin; every signed-in actor reads kits, and
everyone reads the default kit's colours on the public schema. The shipped Angee
kit's values come from its install-tier file, so every kit here starts from the
Angee brand and changes only what a test is about.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from hypothesis import example, given, settings
from hypothesis import strategies as st
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import PermissionAuditEvent, active_relationship_model
from rebac.testing import assert_decisions

from angee.base.build import extension_source_map
from angee.base.fields.text import ColorField
from angee.base.models.identity import public_id_of
from angee.base.testing.permissions import emit_permissions, installed_field_owners
from angee.brandkit import schema as brandkit_schema
from angee.brandkit.fields import FontStackField
from angee.brandkit.models import (
    DEFAULT_DELETE_MESSAGE,
    DEFAULT_UNTICK_MESSAGE,
    LOGO_MESSAGE,
    TEXT_INKS,
    WCAG_TEXT_CONTRAST,
    ContrastWarning,
    MailRole,
    MailScheme,
    MailSeed,
    contrast_ratio,
    mix_colors,
)
from angee.brandkit.models import BrandKit as SourceBrandKit
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema, result_data
from angee.resources.testing.models import Resource
from tests.conftest import File, addon_schema, create_platform_admin, create_user
from tests.decision_table import seat_cells
from tests.test_storage import PNG_BYTES, drive, image_drive  # noqa: F401 -- the image drive logos live on
from tests.test_zed_extensions import _contrib_addon

ROOT = Path(__file__).resolve().parents[1]
THEME_RUNTIME = ROOT / "packages/ui/src/theme/runtime.mjs"
ANGEE_THEME = ROOT / "src/angee/theme_angee/web/src/themes.mjs"
SHIPPED_KIT = ROOT / "src/angee/brandkit/resources/install/010_brandkit.brandkit.yaml"

ANGEE: dict[str, Any] = {
    name: value for name, value in yaml.safe_load(SHIPPED_KIT.read_text())[0].items() if name != "xref"
}
"""The shipped Angee kit's seeded values."""

MID_GREY = "#777777"
"""A brand colour neither of a theme's inks reads on at 4.5:1."""

PALE_YELLOW = "#fff59d"
"""A brand colour too light to colour a link without turning nearly into the text ink."""

COLOR = st.integers(0, 0xFFFFFF).map(lambda value: f"#{value:06x}")


def _kit(name: str, *, is_default: bool = False, **values: Any) -> Any:
    """Create a kit from the Angee values as named system work; the first kit becomes the default."""

    with system_context(reason="tests.brandkit.setup"):
        return BrandKit.objects.create(**{**ANGEE, "name": name, "is_default": is_default, **values})


def _default_names() -> list[str]:
    with system_context(reason="tests.brandkit.read"):
        return list(BrandKit.objects.filter(is_default=True).values_list("name", flat=True))


def _schema() -> Any:
    return addon_schema(brandkit_schema.schemas, "console")


def _public_schema() -> Any:
    return addon_schema(brandkit_schema.schemas, "public")


# ---------- one vocabulary with the theme runtime ----------


def test_kit_colours_are_the_theme_customisation_hex_inputs() -> None:
    """``THEME_COLORS`` is the addon's own colour columns and the theme runtime's hex inputs, in its order.

    A colour column the addon adds fails here until someone decides whether it is a theme
    input; a column a consumer adds by extending the kit is not the addon's own, so it never
    joins ``THEME_COLORS`` or the public type built from it.
    """

    runtime = THEME_RUNTIME.read_text()
    hex_inputs = tuple(re.findall(r'normalizedHex\(value\.(\w+), "\1"\)', runtime))
    own_colours = {field.name for field in SourceBrandKit._meta.fields if isinstance(field, ColorField)}
    assert len(hex_inputs) == 10
    assert SourceBrandKit.THEME_COLORS == hex_inputs
    assert own_colours == set(hex_inputs)
    assert {str(seed) for seed in MailSeed} <= own_colours

    readable_text = re.search(
        r'function readableText\(background\) \{\s*return contrastRatio\("(#[0-9a-f]{6})", background\) >= '
        r'contrastRatio\("(#[0-9a-f]{6})", background\)',
        runtime,
    )
    assert readable_text is not None
    assert readable_text.groups() == TEXT_INKS


def test_the_shipped_kit_states_the_angee_theme_and_no_default() -> None:
    """The shipped kit states the Angee theme's colour defaults and sans stack, and no default flag."""

    theme = ANGEE_THEME.read_text()
    defaults = dict(re.findall(r'(\w+): "(#[0-9a-f]{6})"', theme[theme.index("createThemeCustomizationOptions(") :]))
    sans = re.search(r'"--font-family-sans": "([^"]+)"', theme)
    assert sans is not None
    assert {name: ANGEE[name] for name in defaults} == defaults
    assert ANGEE["font_heading"] == ANGEE["font_body"] == sans.group(1)
    assert "is_default" not in ANGEE


def _runtime_body(runtime: str, function: str) -> str:
    start = runtime.index(f"function {function}(")
    return runtime[start : runtime.index("\nfunction ", start + 1)]


_RUNTIME_MIXES = [
    # (scheme, role, seed, the runtime function, its light or dark block or None, what precedes the mix)
    ("light", "text", "neutral", "applyNeutralPalette", "light", '"--text-primary": '),
    ("light", "muted", "neutral", "applyNeutralPalette", "light", '"--text-muted": '),
    ("light", "border", "neutral", "applyNeutralPalette", "light", '"--border-subtle": '),
    ("dark", "text", "neutral", "applyNeutralPalette", "dark", '"--text-primary": '),
    ("dark", "muted", "neutral", "applyNeutralPalette", "dark", '"--text-muted": '),
    ("dark", "border", "neutral", "applyNeutralPalette", "dark", '"--border-subtle": '),
    ("dark", "canvas", "canvas", "applyCanvasPalette", None, 'dark["--surface-canvas"] = '),
    ("dark", "surface", "surface", "applySurfacePalette", None, "const darkSurface = "),
    ("dark", "primary", "brand", "applyBrandPalette", None, "const darkBrand = "),
]


@pytest.mark.parametrize(("scheme", "role", "seed", "function", "block", "prefix"), _RUNTIME_MIXES)
def test_the_mail_palette_mixes_its_seeds_with_the_theme_runtimes_weights(
    scheme: str, role: str, seed: str, function: str, block: str | None, prefix: str,
) -> None:
    """Each derived role of the Angee kit, which needs no readability move, is the runtime's own mix of its seed."""

    body = _runtime_body(THEME_RUNTIME.read_text(), function)
    if block is not None:
        light, dark = body.split("Object.assign(dark, {", 1)
        body = light if block == "light" else dark
    found = re.search(re.escape(prefix) + r'mixHex\(color, "(#[0-9a-f]{6})", ([0-9.]+)\)', body)
    assert found is not None, f"{function} no longer mixes {prefix.strip()}"
    toward, weight = found.group(1), float(found.group(2))
    assert BrandKit(**ANGEE).mail_palette()[scheme][role] == mix_colors(ANGEE[seed], toward, weight)


# ---------- the font stack field ----------


@pytest.mark.parametrize(
    ("stack", "stored"),
    [
        pytest.param("Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", None, id="theme-stack"),
        pytest.param("Georgia, 'Times New Roman', serif", None, id="single-quoted-family"),
        pytest.param('"Helvetica Neue", Arial, sans-serif', "'Helvetica Neue', Arial, sans-serif",
                     id="double-quotes-stored-single"),
        pytest.param("'Inter\", sans-serif", "'Inter', sans-serif", id="mismatched-quotes-normalised"),
        pytest.param("system-ui", None, id="generic-alone"),
        pytest.param("Inter, SANS-SERIF", None, id="generic-any-case"),
        pytest.param("Noto Sans JP, ヒラギノ角ゴシック, sans-serif", None, id="non-latin-letters"),
        pytest.param("'3M Sans', 'inherit', serif", None, id="quoted-digit-and-keyword"),
    ],
)
def test_a_font_stack_of_named_families_ending_in_a_generic_family_is_admitted(stack: str, stored: str | None) -> None:
    assert FontStackField().clean(stack, None) == (stored or stack)


@pytest.mark.parametrize(
    "stack",
    [
        pytest.param("Inter", id="no-generic-family"),
        pytest.param("Inter, 'sans-serif'", id="quoted-generic-is-a-family-name"),
        pytest.param("Inter, sans-serif;", id="declaration-end"),
        pytest.param("Inter, sans-serif; color: red", id="second-declaration"),
        pytest.param("Inter</style><script>, sans-serif", id="markup"),
        pytest.param("url(x), sans-serif", id="function"),
        pytest.param("'Inter, sans-serif", id="unbalanced-quote"),
        pytest.param("Inter\\, sans-serif", id="css-escape"),
        pytest.param("Inter_Display, sans-serif", id="underscore"),
        pytest.param("Inter\n, sans-serif", id="newline"),
        pytest.param("Inter,, sans-serif", id="empty-family"),
        pytest.param("Inter, sans-serif,", id="trailing-comma"),
        pytest.param("3M Sans, sans-serif", id="bare-family-starting-with-a-digit"),
        pytest.param("inherit, sans-serif", id="css-wide-keyword"),
        pytest.param("Inter, revert-layer, serif", id="css-wide-keyword-among-families"),
        pytest.param("Goudy Bookletter 1911, sans-serif", id="a-later-word-starting-with-a-digit"),
        pytest.param("Hawaii 5-0, sans-serif", id="a-later-word-starting-with-a-digit-and-hyphen"),
        pytest.param("Font Awesome 5 Free, sans-serif", id="a-middle-word-that-is-a-digit"),
        pytest.param("Foo inherit, serif", id="a-later-word-that-is-a-css-wide-keyword"),
        pytest.param("-1abc, serif", id="a-word-starting-with-a-hyphen-and-a-digit"),
        pytest.param("Foo - Bar, serif", id="a-lone-hyphen"),
        pytest.param("", id="empty"),
    ],
)
def test_a_font_stack_that_could_break_out_of_css_or_void_the_rule_is_refused(stack: str) -> None:
    with pytest.raises(ValidationError):
        FontStackField().clean(stack, None)


def test_a_long_run_of_spaces_is_refused_in_linear_time() -> None:
    """The pattern never re-tries a run of spaces: 100,000 of them are refused at once."""

    started = time.perf_counter()
    with pytest.raises(ValidationError):
        FontStackField().clean(" " * 100_000 + "!", None)
    assert time.perf_counter() - started < 0.5


def test_every_write_that_reaches_the_database_checks_the_font_stack(db: None) -> None:
    """``save``, ``bulk_create``, ``bulk_update`` and ``QuerySet.update`` refuse a stack the field does not admit."""

    del db
    kit = _kit("Checked")
    with system_context(reason="tests.brandkit.orm"):
        with pytest.raises(ValidationError), transaction.atomic():
            BrandKit.objects.create(**{**ANGEE, "name": "Created", "font_body": "x</style><script>"})
        with pytest.raises(ValidationError), transaction.atomic():
            BrandKit.objects.bulk_create([BrandKit(**{**ANGEE, "name": "Bulk", "font_body": "Inter"})])
        with pytest.raises(ValidationError), transaction.atomic():
            BrandKit.objects.filter(pk=kit.pk).update(font_heading="a;b")
        stored = BrandKit.objects.get(pk=kit.pk)
        stored.font_body = "inherit, serif"
        with pytest.raises(ValidationError), transaction.atomic():
            BrandKit.objects.bulk_update([stored], ["font_body"])
        BrandKit.objects.filter(pk=kit.pk).update(font_heading='"Helvetica Neue", sans-serif')
        assert BrandKit.objects.get(pk=kit.pk).font_heading == "'Helvetica Neue', sans-serif"


def test_a_kit_refuses_a_bad_colour_and_a_bad_font_stack(db: None) -> None:
    """Both rules are the columns' own validation, so every full clean of a kit applies them."""

    del db
    kit = BrandKit(**{**ANGEE, "name": "Refused", "brand": "e7b008", "font_body": "Inter"})
    with system_context(reason="tests.brandkit.clean"), pytest.raises(ValidationError) as refused:
        kit.full_clean()
    assert {"brand", "font_body"} <= set(refused.value.message_dict)


# ---------- logos ----------


@pytest.mark.usefixtures("image_drive")
def test_a_logo_must_be_an_image_on_the_image_drive(drive: Any) -> None:  # noqa: F811 -- the storage fixture
    with system_context(reason="tests.brandkit.logos"):
        image = File.objects.ingest_image(PNG_BYTES)
        document, _created = File.objects.ingest_bytes(b"not an image", filename="logo.txt", drive_id=str(drive.sqid))
        kit = BrandKit(**{**ANGEE, "name": "Logos", "logo": image, "logo_dark": document})
        with pytest.raises(ValidationError) as refused:
            kit.full_clean()
        assert refused.value.message_dict == {"logo_dark": [LOGO_MESSAGE]}
        kit.logo_dark = image
        kit.full_clean()


@pytest.mark.usefixtures("image_drive")
def test_a_logo_trashed_after_it_was_set_blocks_no_other_edit(db: None) -> None:
    """Only a logo that changes is judged: the kit stays editable, and a new kit cannot take the trashed file."""

    del db
    with system_context(reason="tests.brandkit.logos"):
        image = File.objects.ingest_image(PNG_BYTES)
    kit = _kit("Logos", logo=image)
    with system_context(reason="tests.brandkit.logos"):
        image.delete()  # trashes the file; the kit still points at it
        stored = BrandKit.objects.get(pk=kit.pk)
        stored.accent = "#0e7490"
        stored.full_clean()
        stored.save()
        with pytest.raises(ValidationError) as refused:
            BrandKit(**{**ANGEE, "name": "Another", "logo": image}).full_clean()
    assert refused.value.message_dict == {"logo": [LOGO_MESSAGE]}


# ---------- exactly one default kit ----------


def test_the_first_kit_becomes_the_default_and_no_kit_reads_as_none(db: None) -> None:
    del db
    with system_context(reason="tests.brandkit.read"):
        assert BrandKit.objects.default() is None
    first = _kit("First")
    _kit("Second")
    assert _default_names() == ["First"]
    with system_context(reason="tests.brandkit.read"):
        assert BrandKit.objects.default() == first


def test_saving_a_kit_as_the_default_takes_the_default_from_the_previous_kit(db: None) -> None:
    del db
    admin = create_platform_admin("brandkit-default-admin")
    _kit("First")
    second = _kit("Second")
    assert _default_names() == ["First"]

    with actor_context(admin):
        kit = BrandKit.objects.get(pk=second.pk)
        kit.is_default = True
        kit.save()
        assert BrandKit.objects.default() == kit
    assert _default_names() == ["Second"]


def test_a_save_that_does_not_set_the_default_leaves_it(db: None) -> None:
    del db
    default = _kit("Default")
    other = _kit("Other")
    with system_context(reason="tests.brandkit.edit"):
        other.name = "Renamed"
        other.save()
        default.name = "Still default"
        default.save(update_fields=["name"])
    assert _default_names() == ["Still default"]


def test_the_constraint_refuses_a_second_default_written_past_the_save(db: None) -> None:
    del db
    _kit("First")
    second = _kit("Second")
    with system_context(reason="tests.brandkit.bypass"), pytest.raises(IntegrityError), transaction.atomic():
        BrandKit._base_manager.filter(pk=second.pk).update(is_default=True)


def test_the_default_cannot_be_unticked(db: None) -> None:
    del db
    default = _kit("Default")
    with system_context(reason="tests.brandkit.clean"):
        default.is_default = False
        with pytest.raises(ValidationError) as refused:
            default.full_clean()
    assert refused.value.message_dict == {"is_default": [DEFAULT_UNTICK_MESSAGE]}


def test_the_default_cannot_be_deleted_on_any_delete_path(db: None) -> None:
    """An instance delete, a queryset delete and the console delete all refuse; another kit may go."""

    del db
    default = _kit("Default")
    other = _kit("Other")
    with system_context(reason="tests.brandkit.delete"):
        stale = BrandKit.objects.get(pk=other.pk)
        with pytest.raises(ValidationError, match="Make another kit the default"), transaction.atomic():
            default.delete()
        with pytest.raises(ValidationError), transaction.atomic():
            BrandKit.objects.all().delete()
        other.is_default = True
        other.save()
        with pytest.raises(ValidationError), transaction.atomic():
            stale.delete()  # read before it became the default: the receiver rereads it
        default.refresh_from_db()
        default.delete()
        assert BrandKit.objects.get(pk=other.pk).delete_blocker() == DEFAULT_DELETE_MESSAGE
    assert _default_names() == ["Other"]


def test_taking_the_default_needs_write_on_the_previous_default(
    db: None, composed_permissions: Any, tmp_path: Path,
) -> None:
    """A consumer may grant write on one kit; taking the default still needs write on the kit that holds it."""

    del db, composed_permissions
    configs = list(apps.get_app_configs())
    contrib = _contrib_addon(tmp_path, """definition brandkit/brand_kit {
        relation editor: auth/user
        permission write = editor
    }""", name="brandkit_contrib")
    composed: list[Any] = [*configs, contrib]  # the fragment's addon is an app-config stand-in
    sources = extension_source_map(composed, field_owners=installed_field_owners(configs))
    emit_permissions(configs, tmp_path / "runtime", sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)

    editor = create_user("brandkit-kit-editor")
    _kit("Held")
    edited = _kit("Edited")
    write_relationships([RelationshipTuple(to_object_ref(edited), "editor", to_subject_ref(editor))])
    with actor_context(editor):
        kit = BrandKit.objects.get(pk=edited.pk)
        kit.voice = "Edited by its editor."
        kit.save()
        kit.is_default = True
        with pytest.raises(PermissionDenied, match="write access to the current default kit"), transaction.atomic():
            kit.save()
    assert _default_names() == ["Held"]


# ---------- the mail palette ----------


@pytest.mark.parametrize(
    ("first", "second", "ratio"),
    [
        pytest.param("#000000", "#ffffff", 21.0, id="black-on-white"),
        pytest.param("#ffffff", "#000000", 21.0, id="order-free"),
        pytest.param("#e7b008", "#e7b008", 1.0, id="same-colour"),
        pytest.param(MID_GREY, "#ffffff", 4.48, id="mid-grey-on-white"),
    ],
)
def test_contrast_ratio_is_wcags(first: str, second: str, ratio: float) -> None:
    assert contrast_ratio(first, second) == pytest.approx(ratio, abs=0.005)


def test_the_mail_palette_is_plain_lower_case_colours_for_both_schemes() -> None:
    """Grounds and primary are the Angee seeds as stated, lower-cased; the Angee kit warns of nothing."""

    upper = {name: ANGEE[name].upper() for name in ("brand", "neutral", "canvas", "surface")}
    palette = BrandKit(**{**ANGEE, **upper}).mail_palette()
    assert type(palette) is dict
    assert {scheme: list(roles) for scheme, roles in palette.items()} == {
        str(scheme): [str(role) for role in MailRole] for scheme in MailScheme
    }
    assert all(
        type(value) is str and re.fullmatch(r"#[0-9a-f]{6}", value)
        for roles in palette.values() for value in roles.values()
    )
    light = palette["light"]
    assert (light["canvas"], light["surface"], light["primary"]) == (ANGEE["canvas"], ANGEE["surface"], ANGEE["brand"])
    assert light["muted"] == "#4f4f55"  # the runtime's mix of the neutral; the Angee theme authors #71717a
    assert light["on_primary"] == TEXT_INKS[0]
    assert light["link"] != ANGEE["brand"]  # gold is too light for a link on white: it darkens toward the ink
    assert BrandKit(**ANGEE).contrast_warnings() == []


@settings(max_examples=400, deadline=None)
@given(brand=COLOR, neutral=COLOR, canvas=COLOR, surface=COLOR)
@example(brand=MID_GREY, neutral="#ffffff", canvas="#000000", surface="#ffffff")
@example(brand="#ffffff", neutral="#000000", canvas="#ffffff", surface="#000000")
@example(brand="#000000", neutral=MID_GREY, canvas=MID_GREY, surface=MID_GREY)
@example(brand=PALE_YELLOW, neutral="#fafafa", canvas="#fafaf9", surface="#ffffff")
def test_every_text_colour_of_the_mail_palette_reads_in_both_schemes(
    brand: str, neutral: str, canvas: str, surface: str,
) -> None:
    """Whatever the seeds, text, muted and link read on canvas and surface, and on_primary on primary."""

    seeds = {"brand": brand, "neutral": neutral, "canvas": canvas, "surface": surface}
    palette = BrandKit(**{**ANGEE, **seeds}).mail_palette()
    for roles in palette.values():
        pairs = [(roles[text], roles[ground]) for text in ("text", "muted", "link") for ground in ("canvas", "surface")]
        for foreground, background in [*pairs, (roles["on_primary"], roles["primary"])]:
            assert contrast_ratio(foreground, background) >= WCAG_TEXT_CONTRAST


@pytest.mark.parametrize(
    ("seeds", "warning"),
    [
        pytest.param({"canvas": "#333333"}, ContrastWarning(MailSeed.CANVAS, MailScheme.LIGHT, MailRole.CANVAS),
                     id="a-canvas-too-dark-for-light-mail"),
        pytest.param({"surface": "#1f2937"}, ContrastWarning(MailSeed.SURFACE, MailScheme.LIGHT, MailRole.SURFACE),
                     id="a-surface-too-dark-for-light-mail"),
        pytest.param({"brand": PALE_YELLOW}, ContrastWarning(MailSeed.BRAND, MailScheme.LIGHT, MailRole.LINK),
                     id="a-brand-too-light-to-colour-links"),
    ],
)
def test_a_seed_the_palette_moves_far_is_a_warning(seeds: dict[str, str], warning: ContrastWarning) -> None:
    assert warning in BrandKit(**{**ANGEE, **seeds}).contrast_warnings()


def test_a_seed_stored_past_validation_raises_instead_of_coming_back_as_written(db: None) -> None:
    """The palette parses every seed it reads: trailing markup or a signed channel never reaches mail."""

    del db
    kit = _kit("Bypassed")
    with system_context(reason="tests.brandkit.orm"):
        BrandKit.objects.filter(pk=kit.pk).update(brand="#e7b<b>")  # ColorField checks only in full_clean
        stored = BrandKit.objects.get(pk=kit.pk)
    with pytest.raises(ValidationError):
        stored.mail_palette()
    with pytest.raises(ValidationError):
        BrandKit(**{**ANGEE, "neutral": "#-7-7-7"}).mail_palette()


@pytest.mark.skipif(
    connection.vendor == "postgresql", reason="varchar(7) refuses the eight-character value, so it cannot be stored.",
)
def test_every_palette_colour_is_formatted_from_parsed_channels(db: None) -> None:
    """A trailing newline the hex validator lets past never reaches mail: no role is the stored seed string."""

    del db
    kit = _kit("Newline")
    with system_context(reason="tests.brandkit.orm"):
        BrandKit.objects.filter(pk=kit.pk).update(brand="#E7B008\n")
        stored = BrandKit.objects.get(pk=kit.pk)
    assert stored.brand == "#E7B008\n"
    palette = stored.mail_palette()
    assert palette["light"]["primary"] == "#e7b008"
    assert all(re.fullmatch(r"#[0-9a-f]{6}\Z", value) for roles in palette.values() for value in roles.values())


def test_a_brand_colour_no_ink_reads_on_takes_black_text_and_no_warning() -> None:
    """The theme's two inks fail on a mid grey; black reads wherever white does not."""

    kit = BrandKit(**{**ANGEE, "brand": MID_GREY})
    assert max(contrast_ratio(ink, MID_GREY) for ink in TEXT_INKS) < WCAG_TEXT_CONTRAST
    assert kit.mail_palette()["light"]["on_primary"] == "#000000"
    assert not [warning for warning in kit.contrast_warnings() if warning.role is MailRole.ON_PRIMARY]


# ---------- permissions ----------


def test_the_policy_decides_every_brand_kit_permission(db: None) -> None:
    """Every signed-in actor reads kits and everyone the default kit's colours; only a platform admin manages kits."""

    del db
    seats = {
        "member": create_user("brandkit-policy-member"),
        "admin": create_platform_admin("brandkit-policy-admin"),
        "anonymous": AnonymousUser(),
    }
    default = _kit("Policy")
    other = _kit("Other")
    cells = seat_cells(
        seats.__getitem__,
        (default, "read", "admin member", "anonymous"),
        (other, "read", "admin member", "anonymous"),
        (default, "read_colors", "admin member anonymous", ""),
        (other, "read_colors", "admin member", "anonymous"),
        (default, "create", "admin", "member anonymous"),
        (default, "write", "admin", "member anonymous"),
        (default, "delete", "admin", "member anonymous"),
    )
    assert_decisions(checks=cells, accessible=(
        (seats["anonymous"], "read", BrandKit, ()),
        (seats["anonymous"], "read_colors", BrandKit, (default,)),
    ))
    # The queryset is the enforcement point; the library's `accessible` also lists the filtered
    # constant's `default` sentinel to an actor who reads every kit, which no queryset returns.
    assert set(BrandKit.objects.with_actor(seats["member"])) == {default, other}
    assert list(BrandKit.objects.with_actor(seats["anonymous"])) == []
    assert list(BrandKit.objects.with_actor(seats["anonymous"]).with_action("read_colors")) == [default]


def test_the_colour_disclosure_follows_the_default_column_and_writes_no_tuple(db: None) -> None:
    """Making another kit the default moves the anonymous colour read with it; no relationship row backs it."""

    del db
    anonymous = AnonymousUser()
    first = _kit("First")
    second = _kit("Second")
    assert_decisions(checks=seat_cells(
        {"anonymous": anonymous}.__getitem__,
        (first, "read_colors", "anonymous", ""),
        (second, "read_colors", "", "anonymous"),
    ))

    with system_context(reason="tests.brandkit.default"):
        second.is_default = True
        second.save()
    assert _default_names() == ["Second"]
    assert_decisions(checks=seat_cells(
        {"anonymous": anonymous}.__getitem__,
        (first, "read_colors", "", "anonymous"),
        (second, "read_colors", "anonymous", ""),
    ))
    assert not active_relationship_model().objects.filter(resource_type="brandkit/brand_kit").exists()


# ---------- the console ----------


_INSERT_KIT = """
mutation($object: brand_kits_insert_input!) {
  insert_brand_kits_one(object: $object) { name is_default info contrast_warnings { seed scheme role } }
}
"""
_UPDATE_KIT = """
mutation($id: String!, $set: brand_kits_set_input!) {
  update_brand_kits_by_pk(pk_columns: {id: $id}, _set: $set) { name is_default accent }
}
"""


def _console_kit(**values: Any) -> dict[str, Any]:
    return {**ANGEE, **values}


def _sdl_fields(schema: Any, kind: str, name: str) -> set[str]:
    """The field names of one type or input in a schema's SDL."""

    found = re.search(rf"^{kind} {name} [^{{]*\{{(.*?)^\}}", str(schema), re.MULTILINE | re.DOTALL)
    assert found is not None, f"no {kind} {name}"
    return set(re.findall(r"^\s+(\w+)\s*[(:]", found.group(1), re.MULTILINE))


def test_an_anonymous_caller_reads_no_kit_on_the_console(db: None) -> None:
    """The console's kit reads stay signed-in: the colour disclosure opens nothing there."""

    del db
    assert not {"default_brand_kit", "default_brand_kit_colors"} & _sdl_fields(_schema(), "type", "Query")
    _kit("Angee")
    _kit("Other")
    listed = execute_schema(_schema(), "{ brand_kits { id } }", user=AnonymousUser())
    assert listed.errors is None
    assert listed.data == {"brand_kits": []}


def test_an_admin_writes_a_kit_a_member_and_an_anonymous_caller_cannot(db: None) -> None:
    """Poor contrast is only a warning; a write by anyone but an admin is refused and leaves the default."""

    del db
    admin = create_platform_admin("brandkit-writer")
    member = create_user("brandkit-member")
    _kit("Angee")
    kit = _console_kit(name="Second", is_default=True, brand=PALE_YELLOW, info="#0e7490")

    for refused in (member, AnonymousUser()):
        result = execute_schema(_schema(), _INSERT_KIT, {"object": kit}, user=refused)
        assert result.errors
        assert result.errors[0].extensions["code"] == "PERMISSION_DENIED"  # a valid kit: refused for who, not what
        assert result.data is None or result.data["insert_brand_kits_one"] is None
    assert _default_names() == ["Angee"]

    written = result_data(execute_schema(_schema(), _INSERT_KIT, {"object": kit}, user=admin))
    assert written["insert_brand_kits_one"] == {
        "name": "Second", "is_default": True, "info": "#0e7490",
        "contrast_warnings": [{"seed": "BRAND", "scheme": "LIGHT", "role": "LINK"}],
    }
    assert _default_names() == ["Second"]


def test_the_console_updates_a_kit_but_neither_unticks_nor_deletes_the_default(db: None) -> None:
    del db
    admin = create_platform_admin("brandkit-updater")
    member = create_user("brandkit-onlooker")
    default = _kit("Default")
    other = _kit("Other")

    accent = {"id": public_id_of(other), "set": {"accent": "#0e7490"}}
    assert execute_schema(_schema(), _UPDATE_KIT, accent, user=member).errors
    updated = result_data(execute_schema(_schema(), _UPDATE_KIT, accent, user=admin))
    assert updated["update_brand_kits_by_pk"] == {"name": "Other", "is_default": False, "accent": "#0e7490"}

    untick = execute_schema(
        _schema(), _UPDATE_KIT, {"id": public_id_of(default), "set": {"is_default": False}}, user=admin,
    )
    assert untick.errors
    assert untick.errors[0].extensions["validationErrors"] == {"is_default": [DEFAULT_UNTICK_MESSAGE]}
    delete = execute_schema(
        _schema(), "mutation($id: String!) { delete_brand_kits_by_pk(id: $id) { id } }",
        {"id": public_id_of(default)}, user=admin,
    )
    assert delete.errors
    assert _default_names() == ["Default"]


def test_the_console_reads_the_logos_and_cannot_write_them() -> None:
    """The logos are on the kit's type and absent from every input: no console root writes them."""

    schema = _schema()
    for name in ("brand_kits_insert_input", "brand_kits_set_input"):
        fields = _sdl_fields(schema, "input", name)
        assert "font_body" in fields
        assert not {"logo", "logo_dark"} & fields
    assert {"logo", "logo_dark"} <= _sdl_fields(schema, "type", "BrandKitType")


@pytest.mark.usefixtures("image_drive")
def test_listing_kits_with_logos_costs_the_same_at_one_and_at_twenty_five_rows(db: None) -> None:
    """Every row carries both logos, so the logos' batched reads are part of the count."""

    del db
    member = create_user("brandkit-lister")
    document = "{ brand_kits { name logo { id } logo_dark { id } contrast_warnings { role } } }"

    def add(index: int) -> None:
        with system_context(reason="tests.brandkit.logos"):
            light = File.objects.ingest_image(PNG_BYTES + bytes([index]))
            dark = File.objects.ingest_image(PNG_BYTES + bytes([100 + index]))
        _kit(f"Kit {index}", logo=light, logo_dark=dark)

    def statements(rows: int) -> int:
        with CaptureQueriesContext(connection) as captured:
            listed = result_data(execute_schema(_schema(), document, user=member))["brand_kits"]
        assert len(listed) == rows
        assert all(row["logo"] and row["logo_dark"] for row in listed)
        return len(captured)

    add(0)
    one = statements(1)
    for index in range(1, 25):
        add(index)
    assert statements(25) == one


# ---------- the public schema ----------


THEME_COLORS = SourceBrandKit.THEME_COLORS
_DEFAULT_COLORS = f"{{ default_brand_kit_colors {{ {' '.join(THEME_COLORS)} }} }}"

CLAY: dict[str, Any] = {
    "brand": "#c2410c", "accent": "#0f766e", "neutral": "#78716c", "canvas": "#fafaf8", "surface": "#fffbeb",
    "rail": "#292524", "success": "#15803d", "warning": "#b45309", "danger": "#b91c1c", "info": "#1d4ed8",
}
"""A kit whose ten colours all differ from the Angee kit's; the web fragment's mapping tests use the same."""


def test_everyone_reads_the_default_kits_colours_and_no_kit_reads_as_none(db: None) -> None:
    del db
    member = create_user("brandkit-colour-member")
    for user in (member, AnonymousUser()):
        empty = execute_schema(_public_schema(), _DEFAULT_COLORS, user=user)
        assert empty.errors is None
        assert empty.data == {"default_brand_kit_colors": None}

    _kit("Clay", **CLAY)
    _kit("Angee")
    for user in (member, AnonymousUser()):
        read = result_data(execute_schema(_public_schema(), _DEFAULT_COLORS, user=user))
        assert read["default_brand_kit_colors"] == CLAY


def test_the_colour_read_is_the_disclosure_and_never_a_bypass(db: None) -> None:
    """No bypass, no audit row: the public read decides under ``read_colors``, where a ``sudo()`` read is audited."""

    del db
    _kit("Clay", **CLAY)
    _kit("Angee")
    for user in (create_user("brandkit-colour-audit"), AnonymousUser()):
        audited = PermissionAuditEvent.objects.count()
        read = result_data(execute_schema(_public_schema(), _DEFAULT_COLORS, user=user))
        assert read["default_brand_kit_colors"] == CLAY
        assert PermissionAuditEvent.objects.count() == audited

    audited = PermissionAuditEvent.objects.count()
    with actor_context(AnonymousUser()):
        assert BrandKit.objects.sudo(reason="tests.brandkit.bypass").default() is not None
    assert PermissionAuditEvent.objects.count() == audited + 1


def test_the_public_projection_is_the_theme_inputs_and_nothing_more(db: None) -> None:
    """Only the ten colours of the default kit: no id, name, font, logo, footer, voice or other kit."""

    del db
    schema = _public_schema()
    assert _sdl_fields(schema, "type", "BrandKitColors") == set(THEME_COLORS)
    assert {name for name in _sdl_fields(schema, "type", "Query") if "brand" in name} == {"default_brand_kit_colors"}

    _kit("Angee")
    for field in ("id", "name", "font_body", "logo", "voice", "is_default"):
        refused = execute_schema(schema, f"{{ default_brand_kit_colors {{ {field} }} }}", user=AnonymousUser())
        assert refused.errors
        assert refused.errors[0].message == f"Cannot query field '{field}' on type 'BrandKitColors'."


def test_reading_the_default_kits_colours_costs_the_same_at_one_and_at_twenty_five_kits(db: None) -> None:
    """The same statements at 1 and 25 kits, and the kit's row read selects its id and the ten colours alone."""

    del db
    schema = _public_schema()
    seats = {"member": create_user("brandkit-colour-counter"), "anonymous": AnonymousUser()}
    table = BrandKit._meta.db_table

    def statements() -> dict[str, int]:
        counts = {}
        for seat, user in seats.items():
            execute_schema(schema, _DEFAULT_COLORS, user=user)
            with CaptureQueriesContext(connection) as captured:
                read = result_data(execute_schema(schema, _DEFAULT_COLORS, user=user))
            assert read["default_brand_kit_colors"] == {name: ANGEE[name] for name in THEME_COLORS}
            [kit_read] = [query["sql"] for query in captured.captured_queries if f'FROM "{table}"' in query["sql"]]
            selected = set(re.findall(rf'"{table}"\."(\w+)"', kit_read.split(f' FROM "{table}"', 1)[0]))
            assert selected == {"id", *THEME_COLORS}
            counts[seat] = len(captured)
        return counts

    _kit("Kit 0")
    one = statements()
    for index in range(1, 25):
        _kit(f"Kit {index}", **CLAY)
    assert statements() == one


# ---------- the shipped kit ----------


def test_the_install_tier_ships_the_angee_kit_as_the_first_default_and_a_reload_keeps_edits(db: None) -> None:
    del db
    addon = apps.get_app_config("brandkit")
    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.brandkit.deployment"):
        shipped = BrandKit.objects.default()
        assert shipped is not None
        assert (shipped.name, shipped.brand) == ("Angee", "#e7b008")
        shipped.name = "Renamed"
        shipped.save()
    chosen = _kit("Chosen", is_default=True)

    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert _default_names() == ["Chosen"]
    with system_context(reason="tests.brandkit.read"):
        assert BrandKit.objects.get(pk=shipped.pk).name == "Renamed"
        assert BrandKit.objects.default() == chosen


def test_a_deleted_shipped_kit_comes_back_as_an_ordinary_kit_and_the_chosen_default_stays(db: None) -> None:
    del db
    addon = apps.get_app_config("brandkit")
    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    chosen = _kit("Chosen", is_default=True)
    with system_context(reason="tests.brandkit.deployment"):
        BrandKit.objects.get(name="Angee").delete()

    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert _default_names() == ["Chosen"]
    with system_context(reason="tests.brandkit.read"):
        assert BrandKit.objects.get(name="Angee").is_default is False
        assert BrandKit.objects.default() == chosen
        before = list(BrandKit.objects.order_by("pk").values_list("pk", "name", "is_default", "updated_at"))

    table = BrandKit._meta.db_table
    with CaptureQueriesContext(connection) as captured:
        Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    writes = [
        query["sql"] for query in captured.captured_queries
        if table in query["sql"] and query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []  # the second reload finds the recreated row unchanged and writes no kit row
    with system_context(reason="tests.brandkit.read"):
        assert list(BrandKit.objects.order_by("pk").values_list("pk", "name", "is_default", "updated_at")) == before
