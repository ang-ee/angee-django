"""Model-owned admission for methods offered by decision proposals."""

from typing import ClassVar

from django.db import models


class DecisionTargetMixin(models.Model):
    """Opt into decision method calls without exposing undeclared model verbs."""

    decision_methods: ClassVar[tuple[str, ...]] = ()
    """Methods admitted for proposals; their signatures validate supplied arguments."""

    class Meta:
        abstract = True
