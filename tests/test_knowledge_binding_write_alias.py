"""Knowledge binding validation and publication reload their content type explicitly."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import router
from rebac import ObjectRef

from tests.conftest import RecordBinding, Vault
from tests.test_transitions import TransitionRouter


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("entry", ["clean", "publication", "explicit_publication"])
def test_binding_content_type_ignores_stale_cache_and_read_router(
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    """Validation's pinned alias and publication's explicit alias select the real target."""

    content_type = ContentType.objects.db_manager("default").get_for_model(Vault)
    binding = RecordBinding(pk=7, vault_id=1, content_type_id=content_type.pk, object_id=42)
    stale = ContentType(pk=content_type.pk, app_label="missing_app", model="missing_model")
    binding._state.fields_cache["content_type"] = stale
    with database_alias("binding_writer") as using:
        routing = TransitionRouter("wrong_writer")
        monkeypatch.setattr(router, "routers", [routing])
        binding._state.adding = entry == "clean"
        binding._state.db = "wrong_instance" if entry == "explicit_publication" else using
        if entry == "clean":
            binding.clean()
        elif entry == "explicit_publication":
            assert binding.change_read_resource(using=using) == ObjectRef("knowledge/vault", "42")
        else:
            assert binding.change_read_resource() == ObjectRef("knowledge/vault", "42")
        assert binding._state.fields_cache["content_type"] is stale
        assert routing.writes == []
