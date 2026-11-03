from contextlib import nullcontext

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac.errors import MissingActorError

from angee.base.exceptions import DomainError
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
    from angee.provenance.contrib.angee.exceptions import AngeeChangesetRequired
    from angee.provenance.contrib.angee.exceptions import ChangesetRequired as ProtectedRequired

    assert ProtectedRequired is AngeeChangesetRequired
    assert issubclass(ProtectedRequired, ChangesetRequired)
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


def test_retired_storage_setting_is_refused_even_when_values_agree(settings):
    from angee.provenance.checks import check_configuration

    settings.ANGEE_CHANGESET_MODEL = "provenance.Changeset"
    settings.PROVENANCE_CHANGESET_MODEL = settings.ANGEE_CHANGESET_MODEL
    assert "provenance.E005" in {error.id for error in check_configuration()}
    del settings.PROVENANCE_CHANGESET_MODEL
    assert "provenance.E005" not in {error.id for error in check_configuration()}


def test_creation_conflict_has_one_class_and_domain_refusal_contract():
    from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
    from angee.provenance.engine.exceptions import CreationKeyConflict as StorageConflict

    assert CreationKeyConflict is StorageConflict
    assert isinstance(CreationKeyConflict("duplicate"), DomainError)
    assert not isinstance(CreationKeyConflict("duplicate"), ValidationError)
