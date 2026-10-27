"""Resources-owned projection of source-local catalogue declarations."""

from __future__ import annotations

from django.db import models

from angee.compose.declarations import BuildIntegration, DeclarationValue, ModelDeclaration
from angee.compose.model_composition import ModelComposition
from angee.tiers import ResourceTier


class CatalogueIntegration(BuildIntegration):
    """Preserve explicit catalogue declarations when a source is materialized."""

    def model_declaration(self, composition: ModelComposition, source: type[models.Model]) -> ModelDeclaration:
        """Project only this source's declaration, never its parent's catalogue role."""

        if not source.__dict__.get("catalogue", False):
            return ModelDeclaration()
        attributes: dict[str, DeclarationValue] = {
            "catalogue": True,
            "catalogue_tier": str(source.__dict__.get("catalogue_tier", ResourceTier.MASTER)),
        }
        tiers = source.__dict__.get("catalogue_tiers")
        if tiers is not None:
            attributes["catalogue_tiers"] = (
                tuple(str(tier) if isinstance(tier, str) else tier for tier in tiers)
                if isinstance(tiers, tuple) else tiers
            )
        return ModelDeclaration(attributes=attributes)
