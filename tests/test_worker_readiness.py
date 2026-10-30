"""A failed provision gate prevents every worker blueprint service from starting."""

import pytest
from celery import Celery
from celery.worker import WorkController, components
from django.core.management.base import CommandError

import angee.base
from angee.jobs.bootsteps import RequireProvisioned


@pytest.mark.django_db
def test_provision_failure_aborts_blueprint_initialization(monkeypatch):
    # Celery's Django fixup checks backend/model capabilities before the blueprint.
    started = []
    for owner in (components.Pool, components.Consumer, components.Beat):
        monkeypatch.setattr(owner, 'start', lambda self, worker: started.append(type(self).__name__))

    def refuse(**kwargs):
        assert kwargs == {'using': 'default', 'timeout': 60}
        raise CommandError('provision record is in_progress')

    monkeypatch.setattr(angee.base, 'require_provisioned', refuse, raising=False)
    app = Celery('readiness-gate', broker='memory://', fixups=[])
    app.steps['worker'].add(RequireProvisioned)
    try:
        with pytest.raises(CommandError, match='in_progress'):
            worker = WorkController(app=app, pool='solo', beat=True)
            worker.start()
        assert started == []
    finally:
        app.close()
