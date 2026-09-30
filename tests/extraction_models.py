"""Concrete extraction evidence used by the bare Django test runtime."""

from angee.extraction.models import Extraction as AbstractExtraction
from angee.extraction.models import ExtractionLineage as AbstractExtractionLineage
from angee.extraction.models import ExtractionPage as AbstractExtractionPage
from angee.extraction.models import ExtractionPart as AbstractExtractionPart
from angee.extraction.models import ExtractionSource as AbstractExtractionSource


class ExtractionLineage(AbstractExtractionLineage):
    """Lockable evidence lineage for retention tests."""

    class Meta(AbstractExtractionLineage.Meta):
        abstract = False
        app_label = "extraction"
        db_table = "test_extraction_lineage"


class Extraction(AbstractExtraction):
    """Retained revision using the addon model's complete contract."""

    rebac_grantable = AbstractExtraction.rebac_grantable

    class Meta(AbstractExtraction.Meta):
        abstract = False
        app_label = "extraction"
        db_table = "test_extraction"
        rebac_resource_type = "extraction/extraction"


class ExtractionSource(AbstractExtractionSource):
    """Immutable source identity in a retained revision."""

    class Meta(AbstractExtractionSource.Meta):
        abstract = False
        app_label = "extraction"
        db_table = "test_extraction_source"
        rebac_resource_type = "extraction/extraction_source"


class ExtractionPage(AbstractExtractionPage):
    """Immutable page evidence in a retained revision."""

    class Meta(AbstractExtractionPage.Meta):
        abstract = False
        app_label = "extraction"
        db_table = "test_extraction_page"
        rebac_resource_type = "extraction/extraction_page"


class ExtractionPart(AbstractExtractionPart):
    """Immutable native or recognized carrier in a retained revision."""

    class Meta(AbstractExtractionPart.Meta):
        abstract = False
        app_label = "extraction"
        db_table = "test_extraction_part"
        rebac_resource_type = "extraction/extraction_part"
