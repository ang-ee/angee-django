"""Django makemigrations output for the declared statement cutover schema."""

import django.db.models.manager
from django.db import migrations, models


def applies(state):
    if ("provenance", "changeset") not in state.models:
        return False
    present = {name for name in ("rule", "seed") if ("provenance", name) in state.models}
    if present == {"rule", "seed"}:
        return False
    if present:
        raise ValueError("Statement principal schema is partial; reconcile its native migration state.")
    return True


class Migration(migrations.Migration):

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name='Rule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('key', models.CharField(max_length=255, unique=True)),
            ],
            options={
                'abstract': False,
                'base_manager_name': '_angee_base',
                'default_manager_name': 'objects',
            },
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('_angee_base', django.db.models.manager.Manager()),
            ],
        ),
        migrations.CreateModel(
            name='Seed',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('key', models.CharField(max_length=255, unique=True)),
            ],
            options={
                'abstract': False,
                'base_manager_name': '_angee_base',
                'default_manager_name': 'objects',
            },
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('_angee_base', django.db.models.manager.Manager()),
            ],
        ),
    ]
