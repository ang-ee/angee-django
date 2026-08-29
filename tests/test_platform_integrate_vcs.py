"""The VCS marketplace addon — the ``addon.toml`` catalog parser.

The sync coordination (already-materialised → provenance only; not-materialised →
a REMOTE/DISABLED row; vanished REMOTE → REMOVED) is exercised end to end against a
local VCS bridge in the composed example; this covers the pure parser that turns a
discovered ``addon.toml`` blob into the catalog descriptor the sync upserts.
"""

from __future__ import annotations

from angee.platform_integrate_vcs.catalog import parse_addon_meta


def test_parse_addon_meta_reads_the_addon_block() -> None:
    """The manifest's ``[addon]`` block becomes the catalog descriptor."""

    blob = (
        b'[addon]\nname = "angee.demo"\ndescription = "A demo addon."\n'
        b'depends_on = ["angee.iam", "angee.platform"]\n'
    )

    meta = parse_addon_meta(blob)

    assert meta["name"] == "angee.demo"
    assert meta["label"] == "demo"
    assert meta["namespace"] == "angee"
    assert meta["description"] == "A demo addon."
    assert meta["depends_on"] == ["angee.iam", "angee.platform"]


def test_parse_addon_meta_coerces_a_bare_string_depends_on() -> None:
    """A scalar ``depends_on`` is normalised to a one-element list (manifest convention)."""

    assert parse_addon_meta(b'[addon]\nname = "x.y"\ndepends_on = "x.z"\n')["depends_on"] == ["x.z"]


def test_parse_addon_meta_tolerates_a_bare_manifest() -> None:
    """A manifest with only a name yields empty label/namespace context, no crash."""

    meta = parse_addon_meta(b'[addon]\nname = "solo"\n')

    assert meta["name"] == "solo"
    assert meta["depends_on"] == []
    assert meta["description"] == ""
