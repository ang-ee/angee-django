"""Dashboard trash conflicts."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from angee.base.mixins import StaleRevisionError, require_revision
from angee.dashboards import models as dashboard_models
from angee.dashboards.models import Dashboard


@pytest.mark.django_db
@pytest.mark.parametrize("stale", [False, True])
def test_dashboard_trash_locks_and_checks_revision(monkeypatch: pytest.MonkeyPatch, stale: bool) -> None:
    """Dashboard trash locks and checks revision."""
    locked = Mock(revision=3, is_trashed=False)
    locked.require_revision.side_effect = lambda expected: require_revision(expected=expected, current=locked.revision)
    locked.with_actor.return_value = locked
    rows = Mock()
    rows.get.return_value = locked

    class TrashTarget:
        DashboardScope = Dashboard.DashboardScope
        scope = "personal"
        pk = 7
        _state = SimpleNamespace(db="default", adding=False)
        system_queryset = Mock(return_value=rows)
        require_access = Mock(return_value="actor")

    atomic = Mock(side_effect=lambda **kwargs: nullcontext())
    monkeypatch.setattr(dashboard_models.transaction, "atomic", atomic)
    target = TrashTarget()
    if stale:
        with pytest.raises(StaleRevisionError):
            Dashboard.set_personal_trashed(target, trashed=True, expected_revision=2)
        locked.trash.assert_not_called()
    else:
        Dashboard.set_personal_trashed(target, trashed=True, expected_revision=3)
        locked.trash.assert_called_once_with()
        locked.restore.assert_not_called()
        assert locked.revision == 3
    TrashTarget.system_queryset.assert_called_once_with(lock=("self",))
    atomic.assert_called_once_with()
