"""Concrete extraction models for the root persistence suite."""

from angee.workflows_extraction import models


class Extraction(models.Extraction):
    class Meta(models.Extraction.Meta):
        abstract = False
        app_label = "workflows_extraction"
        db_table = "test_ocr_extraction"
        rebac_resource_type = "workflows_extraction/extraction"


class ExtractionLineage(models.ExtractionLineage):
    class Meta(models.ExtractionLineage.Meta):
        abstract = False
        app_label = "workflows_extraction"
        db_table = "test_ocr_extractionlineage"


class ExtractionSource(models.ExtractionSource):
    class Meta(models.ExtractionSource.Meta):
        abstract = False
        app_label = "workflows_extraction"
        db_table = "test_ocr_extractionsource"
        rebac_resource_type = "workflows_extraction/extraction_source"


class ExtractionPage(models.ExtractionPage):
    class Meta(models.ExtractionPage.Meta):
        abstract = False
        app_label = "workflows_extraction"
        db_table = "test_ocr_extractionpage"
        rebac_resource_type = "workflows_extraction/extraction_page"


class ExtractionPart(models.ExtractionPart):
    class Meta(models.ExtractionPart.Meta):
        abstract = False
        app_label = "workflows_extraction"
        db_table = "test_ocr_extractionpart"
        rebac_resource_type = "workflows_extraction/extraction_part"


OCR_MODELS = (Extraction, ExtractionLineage, ExtractionSource, ExtractionPage, ExtractionPart)
