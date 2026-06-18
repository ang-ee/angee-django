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


class _BoolImpl(ImplBase):
    key = "boolish"
    label = "Boolish"
    defaults = {"login_enabled": True}


class _ConfigBase(ImplBase):
    key = "cfg_base"
    defaults = {"authorize_params": {"host": "base", "port": 1}}


class _ConfigRefined(_ConfigBase):
    key = "cfg_refined"
    defaults = {"authorize_params": {"port": 2, "tls": True}}


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


def test_effective_defaults_deep_merges_dict_defaults() -> None:
    """A dict-valued default merges one level deep along the MRO, not replaced wholesale."""

    assert _ConfigRefined.effective_defaults()["authorize_params"] == {
        "host": "base",  # inherited from the base dict
        "port": 2,  # overridden
        "tls": True,  # added
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


def test_materialize_seeds_only_unprovided_fields() -> None:
    """Materialise fills fields the caller did not supply; a supplied field is kept."""

    client = OAuthClient(authorize_endpoint="https://kept/authorize")
    _RefinedImpl.materialize(client, provided=frozenset({"authorize_endpoint"}))
    assert client.authorize_endpoint == "https://kept/authorize"  # supplied → kept
    assert client.token_endpoint == "https://refined/token"  # unsupplied → seeded
    assert client.userinfo_endpoint == "https://refined/userinfo"  # unsupplied → seeded


def test_materialize_seeds_boolean_default_when_unprovided() -> None:
    """A boolean default lands when omitted (the create-path fix), not just blank scalars."""

    client = OAuthClient()  # login_enabled model default is False
    _BoolImpl.materialize(client, provided=frozenset())
    assert client.login_enabled is True


def test_materialize_keeps_explicit_value_equal_to_default() -> None:
    """A supplied value is never overwritten, even when it equals the model default."""

    client = OAuthClient(login_enabled=False)
    _BoolImpl.materialize(client, provided=frozenset({"login_enabled"}))
    assert client.login_enabled is False  # caller's explicit False survives the impl's True


def test_materialize_deep_copies_mutable_defaults() -> None:
    """Each row gets its own copy of a dict default — never the shared class object."""

    first = OAuthClient()
    second = OAuthClient()
    _ConfigBase.materialize(first, provided=frozenset())
    _ConfigBase.materialize(second, provided=frozenset())
    assert first.authorize_params == second.authorize_params
    assert first.authorize_params is not second.authorize_params
    assert first.authorize_params is not _ConfigBase.defaults["authorize_params"]
