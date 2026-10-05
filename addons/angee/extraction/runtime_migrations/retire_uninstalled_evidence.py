"""Retire old review evidence when extraction is no longer composed."""

from django.db import migrations


def applies(state):
    expected = {"extraction", "extractionlineage", "extractionpage", "extractionpart", "extractionsource"}
    present = {name for label, name in state.models if label == "extraction"}
    if not present:
        return False
    # This cutover is only for the retired lineage/correction-question generation.
    extraction = state.models.get(("extraction", "extraction"))
    if extraction is None or "correction_decision" not in extraction.fields:
        return False
    if present != expected or "head" not in state.models["extraction", "extractionlineage"].fields:
        raise RuntimeError("The retired extraction evidence schema is incomplete.")
    return not any(
        field.is_relation and str(field.remote_field.model).lower().startswith("extraction.")
        for (label, _), model in state.models.items() if label != "extraction"
        for field in model.fields.values()
    )


class Migration(migrations.Migration):
    dependencies = [("extraction", "__latest__")]
    operations = [
        migrations.RemoveField("extractionlineage", "head"),
        migrations.DeleteModel("ExtractionPage"),
        migrations.DeleteModel("ExtractionPart"),
        migrations.DeleteModel("ExtractionSource"),
        migrations.DeleteModel("Extraction"),
        migrations.DeleteModel("ExtractionLineage"),
    ]
