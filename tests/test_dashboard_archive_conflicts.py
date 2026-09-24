"""Dashboard archive conflicts."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from angee.dashboards import models as dashboard_models
from angee.dashboards.models import Dashboard, DashboardConflictError


@pytest.mark.django_db
@pytest.mark.parametrize("stale", [False, True])
def test_dashboard_archive_locks_and_checks_revision(monkeypatch: pytest.MonkeyPatch, stale: bool) -> None:
    """Dashboard archive locks and checks revision."""
    locked = Mock(revision=3, is_archived=False)
    locked.sudo.return_value = locked
    rows = Mock()
    rows.get.return_value = locked

    class ArchiveTarget:
        Scope = Dashboard.Scope
        scope = "personal"
        pk = 7
        _state = SimpleNamespace(db="default", adding=False)
        system_queryset = Mock(return_value=rows)
        actor = Mock(return_value="actor")
        has_access = Mock(return_value=True)

    atomic = Mock(side_effect=lambda **kwargs: nullcontext())
    monkeypatch.setattr(dashboard_models.transaction, "atomic", atomic)
    target = ArchiveTarget()
    if stale:
        with pytest.raises(DashboardConflictError):
            Dashboard.set_personal_archived(target, archived=True, expected_revision=2)
        locked.save.assert_not_called()
    else:
        Dashboard.set_personal_archived(target, archived=True, expected_revision=3)
        locked.save.assert_called_once_with(update_fields=["is_archived", "revision"])
        assert locked.is_archived is True
        assert locked.revision == 4
    ArchiveTarget.system_queryset.assert_called_once_with(lock=("self",))
    atomic.assert_called_once_with()
