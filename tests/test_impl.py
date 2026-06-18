"""Tests for the framework ImplBase: default inheritance, choice metadata, materialise."""

from __future__ import annotations

from angee.base.impl import ImplBase
from tests.conftest import OAuthClient


class _BaseImpl(ImplBase):
    key = "base"
    label = "Base"
    category = "demo"
    defaults = {
        "authorize_endpoint": "https://base/authorize",
        "token_endpoint": "https://base/token",
    }


class _RefinedImpl(_BaseImpl):
    key = "refined"
    defaults = {
        "token_endpoint": "https://refined/token",
        "userinfo_endpoint": "https://refined/userinfo",
    }


def test_effective_defaults_merges_along_mro() -> None:
    """A refinement inherits its base's defaults and overrides only what it restates."""

    assert _RefinedImpl.effective_defaults() == {
        "authorize_endpoint": "https://base/authorize",  # inherited
        "token_endpoint": "https://refined/token",  # overridden
        "userinfo_endpoint": "https://refined/userinfo",  # added
    }
    # The base is unaffected by the refinement's overrides.
    assert _BaseImpl.effective_defaults() == {
        "authorize_endpoint": "https://base/authorize",
        "token_endpoint": "https://base/token",
    }


def test_choice_metadata_falls_back_to_titlecased_key() -> None:
    """``choice`` projects pickable metadata; label falls back to the key, category inherits."""

    assert _RefinedImpl.choice() == {
        "key": "refined",
        "label": "Refined",
        "icon": "",
        "category": "demo",
        "defaults": _RefinedImpl.effective_defaults(),
    }


def test_materialize_seeds_blank_fields_only() -> None:
    """Materialise fills blank fields from the effective defaults, never an existing value."""

    client = OAuthClient(authorize_endpoint="https://kept/authorize")
    _RefinedImpl.materialize(client)
    assert client.authorize_endpoint == "https://kept/authorize"  # already set → kept
    assert client.token_endpoint == "https://refined/token"  # blank → seeded
    assert client.userinfo_endpoint == "https://refined/userinfo"  # blank → seeded


def test_materialize_overwrite_when_not_blank_only() -> None:
    """With ``blank_only=False`` the defaults overwrite existing values."""

    client = OAuthClient(authorize_endpoint="https://kept/authorize")
    _RefinedImpl.materialize(client, blank_only=False)
    assert client.authorize_endpoint == "https://base/authorize"  # overwritten
