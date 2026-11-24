"""Brand kits: a deployment's brand facts, read by themes, mail and documents.

A :class:`BrandKit` states one brand: its name, the colours a customisable theme
takes as inputs, a heading and a body font stack, a logo for light and one for
dark backgrounds, the organization and postal address a mail footer shows, and a
voice note for people and AI drafts. At most one kit is the deployment's default
(:meth:`BrandKitQuerySet.default`).

**Colours are theme inputs.** Each colour column is named exactly as the theme
runtime names a hex customisation input (``parseThemeCustomization`` in
``packages/ui/src/theme/runtime.mjs``), so a kit supplies a customisable theme's
options without a translation table. A kit states light values; a theme derives
its dark values from them.

**Fonts are written into CSS.** :func:`validate_font_stack` admits only family
names and a closing generic family, because HTML autoescaping does not protect a
style.

**Mail works its colours out.** :meth:`BrandKit.mail_palette` derives the
colours a mail layout paints with, for a light and a dark scheme, from the seeds,
as the theme runtime derives the app's tokens, and keeps every text pair readable.
:meth:`BrandKit.contrast_warnings` names the seeds it had to move far to do so;
they are advice, and a save never refuses them.

**Access.** Every signed-in person reads kits; platform administrators manage
them (``permissions.zed``).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import PermissionDenied

from angee.base.fields.text import ColorField
from angee.base.models.base import AngeeDataModel
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

GENERIC_FONT_FAMILIES = frozenset({
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif",
    "ui-monospace", "ui-rounded", "math", "emoji", "fangsong",
})
"""The CSS Fonts 4 generic families; a font stack ends in one, so text always finds a face."""

_FAMILY_NAME = r"[ -]*[^\W_](?:[^\W_]|[ -])*"
_FONT_FAMILY = re.compile(rf" *(?:{_FAMILY_NAME}|'{_FAMILY_NAME}'|\"{_FAMILY_NAME}\") *")


def validate_font_stack(value: str) -> None:
    """Admit a CSS font stack that is safe to write into a style.

    Each comma-separated family is letters, digits, spaces and hyphens, bare or
    wholly inside one pair of single or double quotes; the last is an unquoted CSS
    generic family (:data:`GENERIC_FONT_FAMILIES`). Nothing else can reach the CSS.
    """

    families = value.split(",")
    if not all(_FONT_FAMILY.fullmatch(family) for family in families):
        raise ValidationError(
            "Name each font family with letters, digits, spaces and hyphens, separated by commas.",
            code="font_stack",
        )
    if families[-1].strip().lower() not in GENERIC_FONT_FAMILIES:
        raise ValidationError("End the font stack with a generic family, such as sans-serif.", code="font_stack")


WCAG_TEXT_CONTRAST = 4.5
"""The WCAG 2 AA minimum contrast ratio for body text (success criterion 1.4.3)."""

TEXT_INKS = ("#11141a", "#ffffff")
"""A theme's dark and light ink; on a brand colour it writes in the more readable one.

They mirror ``readableText`` in ``packages/ui/src/theme/runtime.mjs``;
``tests/test_brandkit.py`` keeps the two equal.
"""

FAR_SHIFT = 0.5
"""How far a derived colour may move toward its ink and still read as its seed: past half way it is nearer the ink."""

_SHIFT_STEPS = 100


def relative_luminance(color: str) -> float:
    """Return the WCAG 2 relative luminance of a ``#rrggbb`` colour."""

    red, green, blue = (
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """Return the WCAG 2 contrast ratio of two ``#rrggbb`` colours, from 1 to 21."""

    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def mix_colors(color: str, toward: str, weight: float) -> str:
    """Return ``color`` moved ``weight`` (0 to 1) of the way to ``toward``, channel by channel.

    It mixes as ``mixHex`` in ``packages/ui/src/theme/runtime.mjs`` does, rounding halves up.
    """

    channels = (
        int(int(color[index : index + 2], 16) * (1 - weight) + int(toward[index : index + 2], 16) * weight + 0.5)
        for index in (1, 3, 5)
    )
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def readable_color(color: str, *, on: Sequence[str], ink: str) -> tuple[str, float]:
    """Return ``color`` moved the least toward ``ink`` that reads on every colour in ``on``, and how far it moved.

    Reading means :data:`WCAG_TEXT_CONTRAST`. The move goes in hundredths and ends
    at ``ink`` itself, so the caller passes an ink that reads on every colour in
    ``on``; one that does not is a programming error.
    """

    for step in range(_SHIFT_STEPS + 1):
        shift = step / _SHIFT_STEPS
        candidate = mix_colors(color, ink, shift)
        if all(contrast_ratio(candidate, background) >= WCAG_TEXT_CONTRAST for background in on):
            return candidate, shift
    raise ValueError(f"The ink {ink} does not read on {', '.join(on)}.")


def ink_on(color: str) -> str:
    """Return the ink text is written in on ``color``.

    The more readable of a theme's :data:`TEXT_INKS`; where neither reads, black,
    which reads wherever white does not.
    """

    ink = max(TEXT_INKS, key=lambda candidate: contrast_ratio(candidate, color))
    return ink if contrast_ratio(ink, color) >= WCAG_TEXT_CONTRAST else "#000000"


class MailScheme(StrEnum):
    """A colour scheme of a mail; a message carries both, and the reader's mail client picks one."""

    LIGHT = "light"
    DARK = "dark"


class MailRole(StrEnum):
    """A colour a mail layout paints with, once per scheme."""

    CANVAS = "canvas"
    """The page behind the message."""
    SURFACE = "surface"
    """The card the message sits on."""
    TEXT = "text"
    MUTED = "muted"
    """Secondary text, such as the footer."""
    BORDER = "border"
    PRIMARY = "primary"
    """The fill of a button."""
    ON_PRIMARY = "on_primary"
    """Text on a button."""
    LINK = "link"


@dataclass(frozen=True)
class ContrastWarning:
    """A mail role :meth:`BrandKit.mail_palette` moved far from the kit colour it derives from, to keep it readable."""

    seed: str
    """The kit's colour field the role derives from."""
    scheme: MailScheme
    role: MailRole


@dataclass(frozen=True)
class _MailColor:
    """One worked-out role: its colour, the seed it derives from and how far it moved to read."""

    value: str
    shift: float
    seed: str
    stated: bool = False
    """The role shows the seed as the kit states it, so any move changes the kit's own colour."""

    @property
    def far(self) -> bool:
        """Whether the move took the role from its seed: any move when stated, else past :data:`FAR_SHIFT`."""

        return self.shift > (0 if self.stated else FAR_SHIFT)


def _scheme_colors(
    *, canvas: str, surface: str, text: str, muted: str, border: str, primary: str, ink: str, pole: str, stated: bool,
) -> dict[MailRole, _MailColor]:
    """Work out one scheme's roles from its base colours, the grounds first.

    ``ink`` is the scheme's text ink. A ground it does not read on moves toward
    ``pole`` until it does, so every text colour can then move toward ``ink`` until
    it reads on both grounds. ``stated`` marks the scheme whose canvas, surface and
    muted text are the kit's seeds as stated.
    """

    canvas_color = _MailColor(*readable_color(canvas, on=(ink,), ink=pole), seed="canvas", stated=stated)
    surface_color = _MailColor(*readable_color(surface, on=(ink,), ink=pole), seed="surface", stated=stated)
    grounds = (canvas_color.value, surface_color.value)
    return {
        MailRole.CANVAS: canvas_color,
        MailRole.SURFACE: surface_color,
        MailRole.TEXT: _MailColor(*readable_color(text, on=grounds, ink=ink), seed="neutral"),
        MailRole.MUTED: _MailColor(*readable_color(muted, on=grounds, ink=ink), seed="neutral", stated=stated),
        MailRole.BORDER: _MailColor(border, 0.0, seed="neutral"),
        MailRole.PRIMARY: _MailColor(primary, 0.0, seed="brand"),
        MailRole.ON_PRIMARY: _MailColor(ink_on(primary), 0.0, seed="brand"),
        MailRole.LINK: _MailColor(*readable_color(primary, on=grounds, ink=ink), seed="brand"),
    }


class BrandKitQuerySet[KitT: BrandKit](AngeeHistoryQuerySet[KitT]):
    """Read scopes over brand kits."""

    def default(self) -> KitT | None:
        """Return the default kit among the rows this queryset reads, or ``None``."""

        return self.filter(is_default=True).first()


_BrandKitManagerBase = AngeeHistoryManager.from_queryset(BrandKitQuerySet)


class BrandKitManager[KitT: BrandKit](_BrandKitManagerBase[KitT]):
    """Owns the one-default rule across brand kits."""

    def demote_defaults(self, kit: KitT) -> None:
        """Authorize and clear every other default inside the saving kit's transaction.

        The saving actor must be able to write the kit it takes the default from;
        named system work always may.
        """

        previous = list(
            self.sudo(reason="brandkit.brand_kit.default_integrity")
            .lock_if_supported()
            .filter(is_default=True)
            .exclude(pk=kit.pk)
        )
        actor, unscoped = kit.effective_actor(strict=True)
        for row in previous:
            if not unscoped and not row.with_actor(actor).has_access("write"):
                raise PermissionDenied(
                    "Changing the default brand kit requires write access to the current default kit."
                )
        self.sudo(reason="brandkit.brand_kit.demote_defaults").filter(
            pk__in=[row.pk for row in previous],
        ).update(is_default=False)


class BrandKit(HistoryMixin, AngeeDataModel, concrete=True):
    """One brand: its name, colours, fonts, logos, mail footer and voice."""

    sqid_prefix = "bkt_"

    name = models.CharField(max_length=128)
    """The organization's brand name: the wordmark where no logo shows."""
    is_default = models.BooleanField(default=False)
    """The deployment's kit; saving one as the default clears the previous (:meth:`BrandKitManager.demote_defaults`)."""

    # The theme customisation's hex inputs, named as the theme runtime names them.
    brand = ColorField()
    accent = ColorField()
    neutral = ColorField()
    """The neutral tint: a theme draws muted text in it and derives text and borders from it."""
    canvas = ColorField()
    surface = ColorField()
    rail = ColorField()
    success = ColorField()
    warning = ColorField()
    danger = ColorField()
    info = ColorField()

    font_heading = models.CharField(max_length=255, validators=[validate_font_stack])
    """The CSS font stack headings and the wordmark are set in."""
    font_body = models.CharField(max_length=255, validators=[validate_font_stack])
    """The CSS font stack body text is set in."""

    logo = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="brand_kit_logos",
    )
    """The logo for light backgrounds, a file on storage's image drive every signed-in user reads."""
    logo_dark = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="brand_kit_dark_logos",
    )
    """The logo for dark backgrounds."""

    footer_organization = models.TextField(blank=True, default="")
    """The organization a mail footer names."""
    footer_address = models.TextField(blank=True, default="")
    """The postal address a mail footer shows."""
    voice = models.TextField(blank=True, default="")
    """How the brand writes, for the people and the AI drafts that write in its name."""

    objects = BrandKitManager()

    class Meta:
        """Django model options for a brand kit."""

        abstract = True
        ordering = ("-is_default", "name", "sqid")
        rebac_resource_type = "brandkit/brand_kit"
        constraints = (
            models.UniqueConstraint(
                fields=("is_default",),
                condition=models.Q(is_default=True),
                name="%(app_label)s_brand_kit_one_default",
            ),
        )

    def __str__(self) -> str:
        """Return the brand name for Django displays."""

        return self.name

    def validate_constraints(self, exclude: Collection[str] | None = None) -> None:
        """Leave the one-default constraint to :meth:`save`, which takes the default from the previous kit.

        Checked before the save, the constraint would refuse every new default,
        since the previous kit holds the flag until the save demotes it. The
        database still refuses a second default written past the save.
        """

        super().validate_constraints({*(exclude or ()), "is_default"})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the kit; saved as the default, it becomes the only default."""

        update_fields = kwargs.get("update_fields")
        if not self.is_default or (update_fields is not None and "is_default" not in update_fields):
            super().save(*args, **kwargs)
            return
        with transaction.atomic():
            type(self).objects.demote_defaults(self)
            super().save(*args, **kwargs)

    def mail_palette(self) -> dict[str, dict[str, str]]:
        """Return the colours a mail layout paints with: a ``#rrggbb`` per :class:`MailRole`, per :class:`MailScheme`.

        Worked out from the seeds as the theme runtime works out the app's tokens
        (``packages/ui/src/theme/runtime.mjs``): canvas and surface from their seeds,
        text, muted and border from ``neutral`` (muted text is the neutral tint itself,
        as every customisable theme authors it), primary from ``brand``, on_primary
        the ink that reads on primary, and link the brand colour reading on the
        grounds; each dark value comes from its seed. Each scheme guarantees
        :data:`WCAG_TEXT_CONTRAST` for text, muted and link on canvas and surface and
        for on_primary on primary: a light canvas or surface too dark for the dark ink
        is lightened first, then a text colour that would read less moves the least
        toward the scheme's ink that reads. Plain values, keyed ``"light"`` and
        ``"dark"``, then by role: nothing depends on a request.
        """

        return {
            scheme.value: {role.value: color.value for role, color in roles.items()}
            for scheme, roles in self._mail_colors().items()
        }

    def contrast_warnings(self) -> list[ContrastWarning]:
        """Return the mail roles :meth:`mail_palette` moved far from their seeds to keep them readable.

        A role that shows a seed as the kit states it (the light canvas, surface and
        muted text) is far once it moves at all; a role the palette derives is far
        once it moves past :data:`FAR_SHIFT` toward its ink. The palette repairs every
        pair, so a warning names a seed mail cannot wear as stated. Advice only: a
        save never refuses one.
        """

        return [
            ContrastWarning(seed=color.seed, scheme=scheme, role=role)
            for scheme, roles in self._mail_colors().items()
            for role, color in roles.items()
            if color.far
        ]

    def _mail_colors(self) -> dict[MailScheme, dict[MailRole, _MailColor]]:
        """Work out both schemes' roles, each with the seed it derives from and how far it moved.

        The dark bases are the theme runtime's: ``applyCanvasPalette``,
        ``applySurfacePalette``, ``applyNeutralPalette`` and ``applyBrandPalette``.
        """

        dark_ink, light_ink = TEXT_INKS
        black, white = "#000000", "#ffffff"
        return {
            MailScheme.LIGHT: _scheme_colors(
                canvas=self.canvas,
                surface=self.surface,
                text=mix_colors(self.neutral, black, 0.78),
                muted=self.neutral,
                border=mix_colors(self.neutral, white, 0.78),
                primary=self.brand,
                ink=dark_ink,
                pole=white,
                stated=True,
            ),
            MailScheme.DARK: _scheme_colors(
                canvas=mix_colors(self.canvas, black, 0.94),
                surface=mix_colors(self.surface, black, 0.88),
                text=mix_colors(self.neutral, white, 0.84),
                muted=mix_colors(self.neutral, white, 0.46),
                border=mix_colors(self.neutral, black, 0.60),
                primary=mix_colors(self.brand, white, 0.16),
                ink=light_ink,
                pole=black,
                stated=False,
            ),
        }
