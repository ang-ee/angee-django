from contextlib import nullcontext

import pytest
from django.apps import apps
from rebac.errors import MissingActorError

from angee.provenance.context import ChangesetRequired, changeset, changeset_model_label, current_changeset

pytestmark = [pytest.mark.entrypoint_boundaries, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("opened", [False, True])
def test_disabled_storage_required_fails_closed(settings, opened):
    settings.ANGEE_CHANGESET_MODEL = ""
    with changeset(fresh=True) if opened else nullcontext():
        assert current_changeset() is None
        with pytest.raises(ChangesetRequired):
            current_changeset(required=True)


def test_strictness_is_only_storage_policy(settings, monkeypatch):
    model = apps.get_model(changeset_model_label())
    settings.REBAC_STRICT_MODE = False
    monkeypatch.setattr(model, "context_required", classmethod(lambda cls: True))
    with pytest.raises(ChangesetRequired) as error:
        current_changeset(required=True)
    assert isinstance(error.value, MissingActorError)
    settings.REBAC_STRICT_MODE = True
    monkeypatch.setattr(model, "context_required", classmethod(lambda cls: False))
    assert current_changeset(required=True) is None


def test_generic_context_has_no_host_default_model(settings):
    del settings.ANGEE_CHANGESET_MODEL
    assert changeset_model_label() == ""
    assert current_changeset() is None
    with pytest.raises(ChangesetRequired):
        current_changeset(required=True)
