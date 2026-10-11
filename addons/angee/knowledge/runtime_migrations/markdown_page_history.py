"""Add native history for the body-owning MarkdownPage child.

The table starts empty; legacy Version snapshots are converted separately.
Reverse drops the child's history, including any snapshots recorded since this
migration, while retaining every live page and markdown body.
"""

import angee.base.fields
import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    markdown = state.models.get(("knowledge", "markdownpage"))
    history = state.models.get(("knowledge", "historicalmarkdownpage"))
    if markdown is None:
        if history is not None:
            raise ValueError("MarkdownPage history has no owning child model.")
        return False
    if "page" in markdown.fields and "page_ptr" not in markdown.fields and history is None:
        # The earlier markdown_page_child declaration must run first.
        return False
    link = markdown.fields.get("page_ptr")
    if (
        ("knowledge", "page") not in state.models
        or "page" in markdown.fields
        or not {"body", "body_hash", "kind", "word_count"}.issubset(markdown.fields)
        or not isinstance(link, models.OneToOneField)
        or not link.primary_key
        or not link.remote_field.parent_link
        or link.remote_field.model.lower() != "knowledge.page"
        or "knowledge.page" not in markdown.bases
    ):
        raise ValueError("Complete or reverse the partial MarkdownPage child transition before adding history.")
    if history is None:
        return True
    required = {name for name, _field in Migration.operations[0].fields}
    history_link = history.fields.get("page_ptr")
    if (
        not required.issubset(history.fields)
        or not isinstance(history_link, models.ForeignKey)
        or history_link.remote_field.parent_link
        or history_link.remote_field.model.lower() != "knowledge.page"
    ):
        raise ValueError("Complete or reverse the partial MarkdownPage history schema before upgrading.")
    return False


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('knowledge', '__latest__'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalMarkdownPage',
            fields=[
                ('page_ptr', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='knowledge.page')),
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('is_trashed', models.BooleanField(db_index=True, default=False, editable=False)),
                ('trashed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('trash_reason', models.TextField(blank=True, default='', editable=False)),
                ('title', models.CharField(db_index=True, max_length=512)),
                ('icon', models.CharField(blank=True, default='', max_length=64)),
                ('kind', angee.base.fields.StateField(choices=[('note', 'Note'), ('folder', 'Folder'), ('template', 'Template')], db_index=True, default='note', max_length=8)),
                ('body', models.TextField(blank=True, default='')),
                ('body_hash', models.CharField(blank=True, default='', editable=False, max_length=64)),
                ('word_count', models.PositiveIntegerField(db_index=True, default=0)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('trashed_by', models.ForeignKey(blank=True, db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('vault', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='knowledge.vault')),
                ('parent', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='knowledge.page')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.TextField(null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical markdown page',
                'verbose_name_plural': 'historical markdown pages',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
                'indexes': [],
                'constraints': [],
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
