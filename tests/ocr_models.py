"""Concrete extraction models for the root persistence suite."""

from angee.workflows_ocr import models


class Extraction(models.Extraction):
    class Meta(models.Extraction.Meta):
        abstract = False
        app_label = "workflows_ocr"
        db_table = "test_ocr_extraction"
        rebac_resource_type = "workflows_ocr/extraction"
        rebac_id_attr = "sqid"


class ExtractionSource(models.ExtractionSource):
    class Meta(models.ExtractionSource.Meta):
        abstract = False
        app_label = "workflows_ocr"
        db_table = "test_ocr_extractionsource"
        rebac_resource_type = "workflows_ocr/extraction_source"
        rebac_id_attr = "sqid"


class ExtractionPage(models.ExtractionPage):
    class Meta(models.ExtractionPage.Meta):
        abstract = False
        app_label = "workflows_ocr"
        db_table = "test_ocr_extractionpage"
        rebac_resource_type = "workflows_ocr/extraction_page"
        rebac_id_attr = "sqid"


class ExtractionPart(models.ExtractionPart):
    class Meta(models.ExtractionPart.Meta):
        abstract = False
        app_label = "workflows_ocr"
        db_table = "test_ocr_extractionpart"
        rebac_resource_type = "workflows_ocr/extraction_part"
        rebac_id_attr = "sqid"


OCR_MODELS = (Extraction, ExtractionSource, ExtractionPage, ExtractionPart)
