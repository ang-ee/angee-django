"""Keep health projections consistent through collected delete batches."""

from typing import Any

from rebac.signals import collected_for_delete

from angee.base.signals import connect_for_models
from angee.portfolio.models import Update


def refresh_report_targets(
    sender: type[Update],
    *,
    instances: tuple[Update, ...],
    using: str,
    **kwargs: Any,
) -> None:
    """Recompute each report target from rows surviving this collected delete."""

    del kwargs
    sender.refresh_deleted_targets(instances, using=using)


def connect() -> None:
    """Install the batch hook for composed Update descendants."""

    connect_for_models(
        collected_for_delete,
        refresh_report_targets,
        applies=lambda model: issubclass(model, Update),
        dispatch_uid="portfolio.update.refresh_targets",
    )
