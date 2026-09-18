"""Stage the current extraction schema before retained OCR evidence is adopted.

This source snapshot is materialized only for a complete released
``workflows_ocr`` graph that has no ``workflows_extraction`` model state.  A
fresh installation continues through ordinary native ``makemigrations``; an
already-materialized current graph skips this stage.  Any partial old/current
combination is refused before Django can autodetect destructive changes.
"""

import angee.base.impl
import django.db.models.deletion
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


_OLD = {
    ("workflows_ocr", "extraction"),
    ("workflows_ocr", "extractionsource"),
    ("workflows_ocr", "extractionpage"),
    ("workflows_ocr", "extractionpart"),
}
_CURRENT = {
    ("workflows_extraction", "extraction"),
    ("workflows_extraction", "extractionlineage"),
    ("workflows_extraction", "extractionsource"),
    ("workflows_extraction", "extractionpage"),
    ("workflows_extraction", "extractionpart"),
}


def applies(project_state: ProjectState) -> bool:
    """Create destination tables only for one complete old-only model graph."""

    models = set(project_state.models)
    old_present = _OLD & models
    current_present = _CURRENT & models
    if not old_present and not current_present:
        return False
    if old_present == _OLD and not current_present:
        return True
    if current_present == _CURRENT and not old_present:
        return False
    if old_present == _OLD and current_present == _CURRENT:
        return False
    raise ImproperlyConfigured(
        "angee.workflows_extraction:stage_workflows_extraction found a partial "
        "workflows_ocr/workflows_extraction model graph"
    )


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('agents', '__latest__'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('messaging', '__latest__'),
        ('storage', '__latest__'),
        ('workflows_ocr', '__latest__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Extraction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('revision', models.PositiveIntegerField(default=1, editable=False)),
                ('lineage_key', models.CharField(db_index=True, editable=False, max_length=64)),
                ('reuse_key', models.CharField(editable=False, max_length=64, unique=True)),
                ('status', models.CharField(editable=False, max_length=16)),
                ('error_code', models.CharField(blank=True, editable=False, max_length=100)),
                ('schema_id', models.CharField(editable=False, max_length=255)),
                ('schema_digest', models.CharField(editable=False, max_length=64)),
                ('schema', models.JSONField(editable=False)),
                ('engine', angee.base.impl.ImplClassField(editable=False, max_length=100, registry_setting='ANGEE_EXTRACTION_ENGINE_CLASSES')),
                ('engine_config', models.JSONField(blank=True, default=dict, editable=False)),
                ('result', models.JSONField(editable=False)),
                ('provenance', models.JSONField(default=dict, editable=False)),
                ('document_map', models.JSONField(default=list, editable=False)),
                ('retired_identities', models.JSONField(default=list, editable=False)),
                ('object_id', models.CharField(max_length=255)),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='contenttypes.contenttype')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('model', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='extraction_evidence', to='agents.inferencemodel')),
                ('recognition_model', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='recognition_extraction_evidence', to='agents.inferencemodel')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('lineage_key', '-revision'),
                'abstract': False,
                'base_manager_name': 'objects',
            },
        ),
        migrations.CreateModel(
            name='ExtractionLineage',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('head', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='workflows_extraction.extraction')),
            ],
            options={
                'abstract': False,
                'base_manager_name': 'objects',
            },
        ),
        migrations.CreateModel(
            name='ExtractionSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('position', models.PositiveIntegerField(editable=False)),
                ('content_hash', models.CharField(editable=False, max_length=64)),
                ('extraction', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sources', to='workflows_extraction.extraction')),
                ('file', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='extraction_sources', to='storage.file')),
                ('message_part', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='extraction_sources', to='messaging.part')),
            ],
            options={
                'ordering': ('position',),
                'abstract': False,
                'base_manager_name': 'objects',
            },
        ),
        migrations.CreateModel(
            name='ExtractionPart',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('position', models.PositiveIntegerField(editable=False)),
                ('source_page', models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ('mime_type', models.CharField(editable=False, max_length=128)),
                ('kind', models.CharField(editable=False, max_length=32)),
                ('method', models.CharField(editable=False, max_length=128)),
                ('content_hash', models.CharField(editable=False, max_length=64)),
                ('width', models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ('height', models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ('dpi', models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ('value', models.JSONField(editable=False)),
                ('claims', models.JSONField(blank=True, default=dict, editable=False)),
                ('metadata', models.JSONField(blank=True, default=dict, editable=False)),
                ('duration_ms', models.PositiveIntegerField(default=0, editable=False)),
                ('extraction', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parts', to='workflows_extraction.extraction')),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parts', to='workflows_extraction.extractionsource')),
            ],
            options={
                'ordering': ('position',),
                'abstract': False,
                'base_manager_name': 'objects',
            },
        ),
        migrations.CreateModel(
            name='ExtractionPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('position', models.PositiveIntegerField(editable=False)),
                ('source_page', models.PositiveIntegerField(editable=False)),
                ('width', models.PositiveIntegerField(editable=False)),
                ('height', models.PositiveIntegerField(editable=False)),
                ('dpi', models.PositiveIntegerField(editable=False)),
                ('duration_ms', models.PositiveIntegerField(default=0, editable=False)),
                ('result', models.JSONField(editable=False)),
                ('engine_metadata', models.JSONField(blank=True, default=dict, editable=False)),
                ('extraction', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pages', to='workflows_extraction.extraction')),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pages', to='workflows_extraction.extractionsource')),
            ],
            options={
                'ordering': ('position',),
                'abstract': False,
                'base_manager_name': 'objects',
            },
        ),
        migrations.AddConstraint(
            model_name='extraction',
            constraint=models.UniqueConstraint(fields=('lineage_key', 'revision'), name='uniq_extraction_revision'),
        ),
        migrations.AddConstraint(
            model_name='extractionsource',
            constraint=models.UniqueConstraint(fields=('extraction', 'position'), name='uniq_extraction_source_position'),
        ),
        migrations.AddConstraint(
            model_name='extractionsource',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('file__isnull', False), ('message_part__isnull', True)), models.Q(('file__isnull', True), ('message_part__isnull', False)), _connector='OR'), name='extraction_source_one_input'),
        ),
        migrations.AddConstraint(
            model_name='extractionsource',
            constraint=models.UniqueConstraint(condition=models.Q(('file__isnull', False)), fields=('extraction', 'file'), name='uniq_extraction_source_file'),
        ),
        migrations.AddConstraint(
            model_name='extractionsource',
            constraint=models.UniqueConstraint(condition=models.Q(('message_part__isnull', False)), fields=('extraction', 'message_part'), name='uniq_extraction_source_part'),
        ),
        migrations.AddConstraint(
            model_name='extractionpart',
            constraint=models.UniqueConstraint(fields=('extraction', 'position'), name='uniq_extraction_part_position'),
        ),
        migrations.AddConstraint(
            model_name='extractionpage',
            constraint=models.UniqueConstraint(fields=('extraction', 'position'), name='uniq_extraction_page_position'),
        ),
        migrations.AddConstraint(
            model_name='extractionpage',
            constraint=models.UniqueConstraint(fields=('source', 'source_page'), name='uniq_extraction_source_page'),
        ),
    ]
