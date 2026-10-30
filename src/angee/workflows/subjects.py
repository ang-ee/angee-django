"""Explicit record-owned admission and terminal settlement for root runs."""

from inspect import getattr_static
from typing import Any

from django.apps import apps
from django.core import checks


class RunSubject:
    """Admit and settle root runs about this record, using database work only.

    The engine holds the run lock before locking and loading the subject. Hooks
    share its transaction; admission can refuse with ``ValidationError``. Keep
    settlement a small compare-and-set, without external effects or run locks.
    """

    def admit_run(self, run: Any) -> None:
        """Admit a newly inserted or reopened root run; concrete models implement it."""
        raise NotImplementedError(f"{type(self).__name__} must implement admit_run().")

    def settle_run(self, run: Any, status: str) -> None:
        """Settle the first terminal transition, with the persisted terminal run."""
        raise NotImplementedError(f"{type(self).__name__} must implement settle_run().")


def check_run_subject_models(**kwargs: Any) -> list[checks.CheckMessage]:
    """Reject opted-in models missing either lifecycle hook."""
    del kwargs
    errors: list[checks.CheckMessage] = []
    for model in apps.get_models():
        if not issubclass(model, RunSubject):
            continue
        for name in ("admit_run", "settle_run"):
            method = getattr_static(model, name)
            if method is getattr_static(RunSubject, name) or not callable(method):
                errors.append(checks.Error(
                    f"{model._meta.label} must implement {name}().", obj=model, id="workflows.E002",
                ))
    return errors
