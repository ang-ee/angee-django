"""Brand kits: a deployment's brand facts, read by themes, mail and documents.

A :class:`BrandKit` states one brand: its name, the colours a customisable theme
takes as inputs, a heading and a body font stack, a logo for light and one for
dark backgrounds, the organization and postal address a mail footer shows, and a
voice note for people and AI drafts.

**One default kit.** Once any kit exists, writes through :meth:`BrandKit.save`
and :meth:`BrandKit.clean` keep exactly one default (:meth:`BrandKitQuerySet.default`):
the first kit saved while none is the default takes the flag, saving another kit
as the default takes it from the previous one, and the default can neither be
unticked nor, on any delete path, deleted. Changes of default made at once take
turns (:meth:`BrandKitManager.lock_default`). A write past ``save`` and ``clean``
can leave none.

**Colours are theme inputs.** Each colour column is named exactly as the theme
runtime names a hex customisation input (``parseThemeCustomization`` in
``packages/ui/src/theme/runtime.mjs``), so a kit supplies a customisable theme's
options without a translation table. A kit states light values; a theme derives
its dark values from them.

**Fonts are written into CSS.** :class:`~angee.brandkit.fields.FontStackField`
admits only family names and a closing generic family, on every write, because
HTML autoescaping does not protect a style.

**Mail works its colours out.** :meth:`BrandKit.mail_palette` derives the
colours a mail layout paints with, for a light and a dark scheme, from the seeds,
as the theme runtime derives the app's tokens, and keeps every text pair readable.
:meth:`BrandKit.contrast_warnings` names the seeds it had to move far to do so;
they are advice about mail, and a save never refuses them.

**Access.** Every signed-in person reads kits; platform administrators manage
them (``permissions.zed``).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import PermissionDenied

from angee.base.fields.text import ColorField
from angee.base.models.base import AngeeDataModel
from angee.base.querysets import row_lock_queryset
from angee.base.validators import HEX_COLOR_VALIDATOR
from angee.brandkit.fields import FontStackField
from angee.brandkit.resources import BrandKitResource
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.resources.mixins import ResourceLoadMixin

DEFAULT_DELETE_MESSAGE = "This is the default kit. Make another kit the default before deleting it."
DEFAULT_UNTICK_MESSAGE = "This is the default kit: make another kit the default instead."
LOGO_MESSAGE = "A logo must be an image on storage's image drive."

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


def _parsed_seed(color: str) -> str:
    """Return a seed formatted from its parsed channels as ``#rrggbb``, or raise ``ValidationError``.

    The validator refuses a seed stored past validation; formatting the channels
    back (:func:`mix_colors` at no shift) drops anything the validator lets past
    after them, such as a trailing newline, so no role is ever the stored string.
    """

    HEX_COLOR_VALIDATOR(color)
    return mix_colors(color, color, 0.0)


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


class MailSeed(StrEnum):
    """A kit colour the mail palette derives its roles from; each value is the kit's field name."""

    BRAND = "brand"
    NEUTRAL = "neutral"
    CANVAS = "canvas"
    SURFACE = "surface"


@dataclass(frozen=True)
class ContrastWarning:
    """A mail role :meth:`BrandKit.mail_palette` moved far from the kit colour it derives from, to keep it readable."""

    seed: MailSeed
    scheme: MailScheme
    role: MailRole


@dataclass(frozen=True)
class _MailColor:
    """One worked-out role: its colour, the seed it derives from and how far it moved to read."""

    value: str
    shift: float
    seed: MailSeed
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
    it reads on both grounds. ``stated`` marks the scheme whose canvas and surface
    are the kit's seeds as stated.
    """

    canvas_color = _MailColor(*readable_color(canvas, on=(ink,), ink=pole), seed=MailSeed.CANVAS, stated=stated)
    surface_color = _MailColor(*readable_color(surface, on=(ink,), ink=pole), seed=MailSeed.SURFACE, stated=stated)
    grounds = (canvas_color.value, surface_color.value)
    return {
        MailRole.CANVAS: canvas_color,
        MailRole.SURFACE: surface_color,
        MailRole.TEXT: _MailColor(*readable_color(text, on=grounds, ink=ink), seed=MailSeed.NEUTRAL),
        MailRole.MUTED: _MailColor(*readable_color(muted, on=grounds, ink=ink), seed=MailSeed.NEUTRAL),
        MailRole.BORDER: _MailColor(border, 0.0, seed=MailSeed.NEUTRAL),
        MailRole.PRIMARY: _MailColor(primary, 0.0, seed=MailSeed.BRAND),
        MailRole.ON_PRIMARY: _MailColor(ink_on(primary), 0.0, seed=MailSeed.BRAND),
        MailRole.LINK: _MailColor(*readable_color(primary, on=grounds, ink=ink), seed=MailSeed.BRAND),
    }


class BrandKitQuerySet[KitT: BrandKit](AngeeHistoryQuerySet[KitT]):
    """Read scopes over brand kits."""

    def default(self) -> KitT | None:
        """Return the default kit among the rows this queryset reads, or ``None``.

        ``None`` before any kit exists (a database before its first resource
        load); after a write that goes past :meth:`BrandKit.save` and
        :meth:`BrandKit.clean`, such as ``bulk_create`` into an empty table or
        ``update(is_default=False)``; and for an actor that reads no kit. Writes
        through ``save`` and ``clean`` keep exactly one default once any kit
        exists, so a caller keeps a stated behaviour for ``None``.
        """

        return self.filter(is_default=True).first()


_BrandKitManagerBase = AngeeHistoryManager.from_queryset(BrandKitQuerySet)


class BrandKitManager[KitT: BrandKit](_BrandKitManagerBase[KitT]):
    """Owns the one-default rule across brand kits."""

    def lock_default(self) -> None:
        """Lock every kit, in key order: the lock a write takes before it moves or judges the default.

        No one row stands for the default, so every kit row is the lock. These
        writers take it before any kit row, so they take kit rows in one order and a
        second change of default waits for the first, then reads the default it left:
        a save that can move the default (:meth:`demote_defaults`); every write that
        locks its kit before saving it, the console's and a changeset revert's
        (:meth:`BrandKit.lock_subject`); every delete (:meth:`BrandKit.lock_for_delete`);
        and a resource load that writes kits, once before its first row
        (:class:`~angee.brandkit.resources.BrandKitResource`). A save that cannot
        move the default takes only its own row.

        Two writers take kit rows without it. A file purge clears the logo of every
        kit that shows the file, one column at a time, and the history capture locks
        those kits in scan order; against a change of default it can deadlock, and
        one of the two then fails with a database error. And an empty table has no
        row to lock: of two first kits created at once, the second fails on the
        one-default constraint with a database error, which a person does not reach
        because provisioning loads the first kit.
        """

        list(row_lock_queryset(self.model).order_by("pk").values_list("pk", flat=True))

    def demote_defaults(self, kit: KitT) -> None:
        """Authorize and clear every other default inside the saving kit's transaction, under :meth:`lock_default`.

        The saving actor must be able to write the kit it takes the default from;
        named system work always may.
        """

        self.lock_default()
        previous = list(
            self.sudo(reason="brandkit.brand_kit.default_integrity")
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


class BrandKit(ResourceLoadMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One brand: its name, colours, fonts, logos, mail footer and voice."""

    resource_class = BrandKitResource
    sqid_prefix = "bkt_"

    name = models.CharField(max_length=128)
    """The organization's brand name: the wordmark where no logo shows."""
    is_default = models.BooleanField(default=False)
    """The deployment's kit: exactly one once any kit exists (:meth:`save`, :meth:`clean`, :meth:`delete_blocker`)."""

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

    font_heading = FontStackField()
    """The CSS font stack headings and the wordmark are set in."""
    font_body = FontStackField()
    """The CSS font stack body text is set in."""

    logo = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="brand_kit_logos",
    )
    """The logo for light backgrounds: an image storage's ``FileManager.ingest_image`` put on its image drive.

    Every signed-in user reads the image drive, so every kit reader sees the logo.
    :meth:`clean` refuses any other file. No console writer exists yet: the console
    reads the logos and writes neither.
    """
    logo_dark = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="brand_kit_dark_logos",
    )
    """The logo for dark backgrounds, held to the same rule as :attr:`logo`."""

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

    def clean(self) -> None:
        """Refuse unticking the default kit, and a new logo that is not an image on storage's image drive.

        A logo is judged only when it differs from the stored row's, so a logo
        trashed after it was set never refuses an unrelated edit of the kit.
        """

        super().clean()
        errors: dict[str, str] = {}
        stored = None if self._state.adding else (
            type(self).system_queryset().filter(pk=self.pk).values("is_default", "logo_id", "logo_dark_id").first()
        )
        if stored is not None and stored["is_default"] and not self.is_default:
            errors["is_default"] = DEFAULT_UNTICK_MESSAGE
        images = apps.get_model("storage", "File").system_queryset().images()
        stored_logos = {} if stored is None else {"logo": stored["logo_id"], "logo_dark": stored["logo_dark_id"]}
        for field in ("logo", "logo_dark"):
            file_id = getattr(self, f"{field}_id")
            changed = field not in stored_logos or file_id != stored_logos[field]
            if file_id is not None and changed and not images.filter(pk=file_id).exists():
                errors[field] = LOGO_MESSAGE
        if errors:
            raise ValidationError(errors)

    def delete_blocker(self, *, cascaded: bool = False) -> str | None:
        """Refuse deleting the default kit, wherever the delete starts: exactly one kit stays the default.

        Enforced on instance, queryset and cascade deletes by
        :func:`angee.brandkit.signals.refuse_default_delete`.
        """

        del cascaded
        return DEFAULT_DELETE_MESSAGE if self.is_default else None

    def lock_for_delete(self, *, queryset: models.QuerySet[models.Model] | None = None) -> Self | None:
        """Lock every kit, then this one, so a delete judges the default under :meth:`BrandKitManager.lock_default`."""

        type(self).objects.lock_default()
        return super().lock_for_delete(queryset=queryset)

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Self:
        """Lock every kit, then this one (:meth:`BrandKitManager.lock_default`).

        A writer that locks its kit before saving it, the console's or a changeset
        revert's, cannot know before the lock whether the kit is the default by the
        time it saves, so it always takes every kit first, in the order every change
        of default takes.
        """

        type(self).objects.lock_default()
        return super().lock_subject(members)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the kit, keeping exactly one default.

        The first kit saved while no kit is the default takes the flag; a kit saved
        as the default takes it from the previous one
        (:meth:`BrandKitManager.demote_defaults`). A stored kit saved as not the
        default is refused if, read under its row lock, it is the default, as
        :meth:`clean` refuses it before the save: an edit cleaned before the kit
        became the default never unticks it.
        """

        update_fields = kwargs.get("update_fields")
        writes_default = update_fields is None or "is_default" in update_fields
        with transaction.atomic():
            if self._state.adding and not self.is_default:
                self.is_default = not type(self).system_queryset().filter(is_default=True).exists()
            if self.is_default and writes_default:
                type(self).objects.demote_defaults(self)
            elif writes_default and not self._state.adding and (
                row_lock_queryset(type(self)).filter(pk=self.pk).values_list("is_default", flat=True).first()
            ):
                raise ValidationError({"is_default": DEFAULT_UNTICK_MESSAGE})
            super().save(*args, **kwargs)

    def mail_palette(self) -> dict[str, dict[str, str]]:
        """Return the colours a mail layout paints with: a ``#rrggbb`` per :class:`MailRole`, per :class:`MailScheme`.

        Worked out from the lower-cased seeds. The light canvas, surface and primary
        are the kit's ``canvas``, ``surface`` and ``brand`` as stated. Every other
        role follows the theme runtime's derivation for a customised theme
        (``packages/ui/src/theme/runtime.mjs``): light text, muted and border from
        ``neutral`` (``applyNeutralPalette``); every dark value from its seed
        (``applyCanvasPalette``, ``applySurfacePalette``, ``applyNeutralPalette``,
        ``applyBrandPalette``); on_primary the ink that reads on primary
        (``readableText``, else black); link the primary colour made readable on the
        grounds (as ``readableTintText`` does). Each scheme guarantees
        :data:`WCAG_TEXT_CONTRAST` for text, muted and link on canvas and surface and
        for on_primary on primary: a light canvas or surface too dark for the dark ink
        is lightened first, then a text colour that would read less moves the least
        toward the scheme's ink that reads. Plain values, keyed ``"light"`` and
        ``"dark"``, then by role, every colour ``#rrggbb`` in lower case: nothing
        depends on a request. A seed stored past validation (an ORM write that
        skips ``full_clean``) raises ``ValidationError``; it never comes back as
        written.
        """

        return {
            scheme.value: {role.value: color.value for role, color in roles.items()}
            for scheme, roles in self._mail_colors().items()
        }

    def contrast_warnings(self) -> list[ContrastWarning]:
        """Return the mail roles :meth:`mail_palette` moved far from their seeds to keep them readable.

        A role that shows a seed as the kit states it (the light canvas and surface)
        is far once it moves at all; a role the palette derives is far once it moves
        past :data:`FAR_SHIFT` toward its ink. The palette repairs every pair, so a
        warning names a seed mail cannot wear as stated. It covers mail only, not the
        app a theme dresses. Advice only: a save never refuses one.
        """

        return [
            ContrastWarning(seed=color.seed, scheme=scheme, role=role)
            for scheme, roles in self._mail_colors().items()
            for role, color in roles.items()
            if color.far
        ]

    def _mail_colors(self) -> dict[MailScheme, dict[MailRole, _MailColor]]:
        """Work out both schemes' roles, each with the seed it derives from and how far it moved.

        The mix weights are the theme runtime's (``tests/test_brandkit.py`` reads
        them from ``runtime.mjs`` and compares).
        """

        dark_ink, light_ink = TEXT_INKS
        black, white = "#000000", "#ffffff"
        brand, neutral, canvas, surface = (
            _parsed_seed(seed) for seed in (self.brand, self.neutral, self.canvas, self.surface)
        )
        return {
            MailScheme.LIGHT: _scheme_colors(
                canvas=canvas,
                surface=surface,
                text=mix_colors(neutral, black, 0.78),
                muted=mix_colors(neutral, black, 0.30),
                border=mix_colors(neutral, white, 0.78),
                primary=brand,
                ink=dark_ink,
                pole=white,
                stated=True,
            ),
            MailScheme.DARK: _scheme_colors(
                canvas=mix_colors(canvas, black, 0.94),
                surface=mix_colors(surface, black, 0.88),
                text=mix_colors(neutral, white, 0.84),
                muted=mix_colors(neutral, white, 0.46),
                border=mix_colors(neutral, black, 0.60),
                primary=mix_colors(brand, white, 0.16),
                ink=light_ink,
                pole=black,
                stated=False,
            ),
        }
