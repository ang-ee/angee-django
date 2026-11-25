"""Canonical concrete brand kit model for the bare-Django test runtime.

One concrete model per resource type; the composed runtime emits its own.
"""

from angee.brandkit.models import BrandKit as AbstractBrandKit


class BrandKit(AbstractBrandKit):
    """Concrete brand kit used by the brandkit tests."""

    class Meta(AbstractBrandKit.Meta):
        """Django model options for the canonical test brand kit."""

        abstract = False
        app_label = "brandkit"
        db_table = "test_brandkit_brand_kit"
        rebac_resource_type = "brandkit/brand_kit"
