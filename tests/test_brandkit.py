"""Brand kits: theme-named colours, the font-stack rule, one default kit, the mail palette and who may do what.

Kits are deployment configuration: setup rows are written under ``system_context``
and console writes run as a platform admin; every signed-in actor reads kits. The
shipped Angee kit's values come from its install-tier file, so every kit here
starts from the Angee brand and changes only what a test is about.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from hypothesis import example, given, settings
from hypothesis import strategies as st
from rebac import actor_context, system_context
from rebac.testing import assert_decisions

from angee.base.fields.text import ColorField
from angee.brandkit import schema as brandkit_schema
from angee.brandkit.models import (
    TEXT_INKS,
    WCAG_TEXT_CONTRAST,
    ContrastWarning,
    MailRole,
    MailScheme,
    contrast_ratio,
    mix_colors,
    validate_font_stack,
)
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema, result_data
from angee.resources.testing.models import Resource
from tests.conftest import addon_schema, create_platform_admin, create_user
from tests.decision_table import seat_cells

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
    """Create a kit from the Angee values as named system work."""

    with system_context(reason="tests.brandkit.setup"):
        return BrandKit.objects.create(**{**ANGEE, "name": name, "is_default": is_default, **values})


def _default_names() -> list[str]:
    with system_context(reason="tests.brandkit.read"):
        return list(BrandKit.objects.filter(is_default=True).values_list("name", flat=True))


def test_kit_colours_are_the_theme_customisation_hex_inputs() -> None:
    """The kit's colour columns and the theme runtime's hex inputs are one vocabulary, and its inks are the theme's."""

    runtime = THEME_RUNTIME.read_text()
    hex_inputs = set(re.findall(r'normalizedHex\(value\.(\w+), "\1"\)', runtime))
    colours = {field.name for field in BrandKit._meta.concrete_fields if isinstance(field, ColorField)}
    assert hex_inputs
    assert colours == hex_inputs

    readable_text = re.search(
        r'function readableText\(background\) \{\s*return contrastRatio\("(#[0-9a-f]{6})", background\) >= '
        r'contrastRatio\("(#[0-9a-f]{6})", background\)',
        runtime,
    )
    assert readable_text is not None
    assert readable_text.groups() == TEXT_INKS


def test_the_shipped_kit_states_the_angee_theme() -> None:
    """The default kit's colours are the Angee theme's customisation defaults and its fonts that theme's sans stack."""

    theme = ANGEE_THEME.read_text()
    defaults = dict(re.findall(r'(\w+): "(#[0-9a-f]{6})"', theme[theme.index("createThemeCustomizationOptions(") :]))
    sans = re.search(r'"--font-family-sans": "([^"]+)"', theme)
    assert sans is not None
    assert {name: ANGEE[name] for name in defaults} == defaults
    assert ANGEE["font_heading"] == ANGEE["font_body"] == sans.group(1)
    assert ANGEE["is_default"] is True


@pytest.mark.parametrize(
    "stack",
    [
        pytest.param("Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", id="theme-stack"),
        pytest.param("Georgia, 'Times New Roman', serif", id="single-quoted-family"),
        pytest.param('"Helvetica Neue", Arial, sans-serif', id="double-quoted-family"),
        pytest.param("system-ui", id="generic-alone"),
        pytest.param("Inter, SANS-SERIF", id="generic-any-case"),
        pytest.param("Noto Sans JP, ヒラギノ角ゴシック, sans-serif", id="non-latin-letters"),
    ],
)
def test_a_font_stack_of_named_families_ending_in_a_generic_family_is_admitted(stack: str) -> None:
    validate_font_stack(stack)


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
        pytest.param("'Inter\", sans-serif", id="mismatched-quotes"),
        pytest.param("Inter\\, sans-serif", id="css-escape"),
        pytest.param("Inter_Display, sans-serif", id="underscore"),
        pytest.param("Inter\n, sans-serif", id="newline"),
        pytest.param("Inter,, sans-serif", id="empty-family"),
        pytest.param("Inter, sans-serif,", id="trailing-comma"),
        pytest.param("", id="empty"),
    ],
)
def test_a_font_stack_that_could_break_out_of_css_is_refused(stack: str) -> None:
    with pytest.raises(ValidationError):
        validate_font_stack(stack)


def test_a_kit_refuses_a_bad_colour_and_a_bad_font_stack(db: None) -> None:
    """Both rules are the columns' own validation, so every full clean of a kit applies them."""

    del db
    kit = BrandKit(**{**ANGEE, "name": "Refused", "brand": "e7b008", "font_body": "Inter"})
    with system_context(reason="tests.brandkit.clean"), pytest.raises(ValidationError) as refused:
        kit.full_clean()
    assert {"brand", "font_body"} <= set(refused.value.message_dict)


def test_saving_a_kit_as_the_default_takes_the_default_from_the_previous_kit(db: None) -> None:
    del db
    admin = create_platform_admin("brandkit-default-admin")
    _kit("First", is_default=True)
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
    default = _kit("Default", is_default=True)
    other = _kit("Other")
    with system_context(reason="tests.brandkit.edit"):
        other.name = "Renamed"
        other.save()
        default.is_default = True
        default.name = "Still default"
        default.save(update_fields=["name"])
    assert _default_names() == ["Still default"]


def test_no_default_kit_reads_as_none(db: None) -> None:
    del db
    _kit("Not the default")
    with system_context(reason="tests.brandkit.read"):
        assert BrandKit.objects.default() is None


def test_the_constraint_refuses_a_second_default_written_past_the_save(db: None) -> None:
    del db
    _kit("First", is_default=True)
    second = _kit("Second")
    with system_context(reason="tests.brandkit.bypass"), pytest.raises(IntegrityError), transaction.atomic():
        BrandKit._base_manager.filter(pk=second.pk).update(is_default=True)


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


def test_the_mail_palette_is_plain_colours_for_both_schemes_worked_out_from_the_seeds() -> None:
    """The Angee kit reads as stated: grounds, muted text and primary are its seeds; the dark scheme comes from them."""

    palette = BrandKit(**ANGEE).mail_palette()
    assert type(palette) is dict
    assert {scheme: list(roles) for scheme, roles in palette.items()} == {
        str(scheme): [str(role) for role in MailRole] for scheme in MailScheme
    }
    assert all(type(value) is str and re.fullmatch(r"#[0-9a-f]{6}", value) for roles in palette.values()
               for value in roles.values())
    light, dark = palette["light"], palette["dark"]
    assert (light["canvas"], light["surface"], light["muted"], light["primary"]) == (
        ANGEE["canvas"], ANGEE["surface"], ANGEE["neutral"], ANGEE["brand"],
    )
    assert light["on_primary"] == TEXT_INKS[0]
    assert light["link"] != ANGEE["brand"]  # gold is too light for a link on white: it darkens toward the ink
    assert dark["canvas"] == mix_colors(ANGEE["canvas"], "#000000", 0.94)
    assert dark["primary"] == mix_colors(ANGEE["brand"], "#ffffff", 0.16)
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
        pytest.param({"canvas": "#333333"}, ContrastWarning("canvas", MailScheme.LIGHT, MailRole.CANVAS),
                     id="a-canvas-too-dark-for-light-mail"),
        pytest.param({"surface": "#1f2937"}, ContrastWarning("surface", MailScheme.LIGHT, MailRole.SURFACE),
                     id="a-surface-too-dark-for-light-mail"),
        pytest.param({"neutral": "#d4d4d8"}, ContrastWarning("neutral", MailScheme.LIGHT, MailRole.MUTED),
                     id="a-neutral-too-light-for-muted-text"),
        pytest.param({"brand": PALE_YELLOW}, ContrastWarning("brand", MailScheme.LIGHT, MailRole.LINK),
                     id="a-brand-too-light-to-colour-links"),
    ],
)
def test_a_seed_the_palette_moves_far_is_a_warning(seeds: dict[str, str], warning: ContrastWarning) -> None:
    assert warning in BrandKit(**{**ANGEE, **seeds}).contrast_warnings()


def test_a_brand_colour_no_ink_reads_on_takes_black_text_and_no_warning() -> None:
    """The theme's two inks fail on a mid grey; black reads wherever white does not."""

    kit = BrandKit(**{**ANGEE, "brand": MID_GREY})
    assert max(contrast_ratio(ink, MID_GREY) for ink in TEXT_INKS) < WCAG_TEXT_CONTRAST
    assert kit.mail_palette()["light"]["on_primary"] == "#000000"
    assert not [warning for warning in kit.contrast_warnings() if warning.role is MailRole.ON_PRIMARY]


def test_the_policy_decides_every_brand_kit_permission(db: None) -> None:
    """Every signed-in actor reads kits; only a platform admin creates, edits or deletes one."""

    del db
    seats = {
        "member": create_user("brandkit-policy-member"),
        "admin": create_platform_admin("brandkit-policy-admin"),
        "anonymous": AnonymousUser(),
    }
    kit = _kit("Policy", is_default=True)
    cells = seat_cells(
        seats.__getitem__,
        (kit, "read", "admin member", "anonymous"),
        (kit, "create", "admin", "member anonymous"),
        (kit, "write", "admin", "member anonymous"),
        (kit, "delete", "admin", "member anonymous"),
    )
    assert_decisions(checks=cells, accessible=((seats["member"], "read", BrandKit, (kit,)),))


_DEFAULT_KIT = "{ default_brand_kit { name is_default brand contrast_warnings { seed scheme role } } }"
_INSERT_KIT = """
mutation($object: brand_kits_insert_input!) {
  insert_brand_kits_one(object: $object) { name is_default info contrast_warnings { seed scheme role } }
}
"""


def test_a_member_reads_the_default_kit_and_an_anonymous_caller_reads_none(db: None) -> None:
    del db
    member = create_user("brandkit-reader")
    _kit("Angee", is_default=True)
    _kit("Other")
    schema = addon_schema(brandkit_schema.schemas, "console")

    read = result_data(execute_schema(schema, _DEFAULT_KIT, user=member))
    assert read["default_brand_kit"] == {
        "name": "Angee", "is_default": True, "brand": "#e7b008", "contrast_warnings": [],
    }
    anonymous = execute_schema(schema, _DEFAULT_KIT, user=AnonymousUser())
    assert anonymous.errors or anonymous.data["default_brand_kit"] is None


def test_an_admin_writes_a_kit_a_member_cannot_and_poor_contrast_is_only_a_warning(db: None) -> None:
    del db
    admin = create_platform_admin("brandkit-writer")
    member = create_user("brandkit-member")
    _kit("Angee", is_default=True)
    schema = addon_schema(brandkit_schema.schemas, "console")
    kit = {**ANGEE, "name": "Second", "is_default": True, "brand": PALE_YELLOW, "info": "#0e7490"}

    assert execute_schema(schema, _INSERT_KIT, {"object": kit}, user=member).errors
    assert _default_names() == ["Angee"]

    written = result_data(execute_schema(schema, _INSERT_KIT, {"object": kit}, user=admin))
    assert written["insert_brand_kits_one"] == {
        "name": "Second", "is_default": True, "info": "#0e7490",
        "contrast_warnings": [{"seed": "brand", "scheme": "LIGHT", "role": "LINK"}],
    }
    assert _default_names() == ["Second"]


def test_listing_kits_costs_the_same_at_one_and_at_twenty_five_rows(db: None) -> None:
    del db
    member = create_user("brandkit-lister")
    schema = addon_schema(brandkit_schema.schemas, "console")
    document = "{ brand_kits { name logo { id } logo_dark { id } contrast_warnings { role } } }"

    def statements(rows: int) -> int:
        with CaptureQueriesContext(connection) as captured:
            listed = result_data(execute_schema(schema, document, user=member))["brand_kits"]
        assert len(listed) == rows
        return len(captured)

    _kit("Kit 0")
    one = statements(1)
    for index in range(1, 25):
        _kit(f"Kit {index}")
    assert statements(25) == one


def test_the_install_tier_ships_the_default_angee_kit_and_a_reload_keeps_the_deployments_choice(db: None) -> None:
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
