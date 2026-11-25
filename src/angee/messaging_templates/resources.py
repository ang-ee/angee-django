"""Resource declarations install shipped message templates as baselines."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ValidationError
from django.db import models

from angee.resources.loader import AngeeResource

if TYPE_CHECKING:
    from angee.messaging_templates.models import MessageTemplate

_DERIVED_COLUMNS = ("is_baseline", "content_hash", "baseline_hash")


class MessageTemplateResource(AngeeResource):
    """Install every row as a baseline; a row never states the flag or the hashes, which the model derives.

    No adoption: the baseline constraint is conditional, so a natural key could
    adopt the deployment's override instead of the baseline.
    """

    def before_import_row(self, row: dict[str, Any], **kwargs: Any) -> None:
        """Refuse a row that states the baseline flag or a hash."""

        if stated := [column for column in _DERIVED_COLUMNS if column in row]:
            raise ValidationError(dict.fromkeys(stated, "Message template resources never state this column."))
        super().before_import_row(row, **kwargs)

    def init_instance(self, row: Mapping[str, Any] | None = None) -> models.Model:
        """Construct a new row as a baseline."""

        instance = cast("MessageTemplate", super().init_instance(row))
        instance.is_baseline = True
        return instance
