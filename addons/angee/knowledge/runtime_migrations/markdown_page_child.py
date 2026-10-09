"""Make a released MarkdownPage the Page child it now is.

Released histories store markdown in a separate model linked one-to-one through
``page``, with its own key, audit columns and no ``kind``; the page kind lives on
Page. The current model is a materialized child of Page keyed by ``page_ptr``,
whose ``kind`` says note or template. This migration rebuilds the table as that
child, keyed by its page's id, carrying each page's kind onto its markdown row.
Nothing references the old markdown key, so no other table changes. The child's
own audit columns go: a child inherits them from its page, which (with its
history) stops declaring ``kind``.
"""

from django.db import migrations, models
from django.db.migrations.state import ModelState

from angee.base.fields import StateField

KINDS = (("note", "Note"), ("folder", "Folder"), ("template", "Template"))


def applies(state):
    markdown = state.models.get(("knowledge", "markdownpage"))
    if markdown is None:
        return False
    fields = markdown.fields
    if "page" in fields and "page_ptr" not in fields:
        return True
    if "page_ptr" in fields and "page" not in fields:
        return False
    raise ValueError("MarkdownPage has a partial page-child transition; complete or reverse its migration history.")


def _lookups(node):
    """Yield the field names a Q condition's lookups start from."""

    for child in node.children:
        if isinstance(child, tuple):
            yield child[0].split("__")[0]
        elif isinstance(child, models.Q):
            yield from _lookups(child)


def _uses(item, removed):
    """Whether a constraint or index names a field in ``removed``."""

    names = set(getattr(item, "fields", ()) or ())
    condition = getattr(item, "condition", None)
    if isinstance(condition, models.Q):
        names.update(_lookups(condition))
    return bool(names & removed)


def _kind_retirements(state, app_label):
    """Take ``kind`` off Page and its history, constraints and indexes first."""

    operations = []
    for name in ("page", "historicalpage"):
        model = state.models.get((app_label, name))
        if model is None or "kind" not in model.fields:
            continue
        for key, operation in (("constraints", migrations.RemoveConstraint), ("indexes", migrations.RemoveIndex)):
            operations.extend(
                operation(name, item.name) for item in model.options.get(key, ()) if _uses(item, {"kind"})
            )
        operations.append(migrations.RemoveField(name, "kind"))
    return operations


class RebuildMarkdownPageAsChild(migrations.operations.base.Operation):
    """Replace the linked markdown table with a Page child keyed by the page id."""

    reduces_to_sql = False
    reversible = False

    def state_forwards(self, app_label, state):
        old = state.models[(app_label, "markdownpage")]
        page = state.models[(app_label, "page")]
        removed = {"id", "page", *page.fields}
        fields = [
            (
                "page_ptr",
                models.OneToOneField(
                    auto_created=True,
                    on_delete=models.deletion.CASCADE,
                    parent_link=True,
                    primary_key=True,
                    serialize=False,
                    to="knowledge.page",
                ),
            ),
            *((name, field.clone()) for name, field in old.fields.items() if name not in removed),
            ("kind", StateField(choices=KINDS, default="note", max_length=32)),
        ]
        options = dict(old.options)
        for key in ("constraints", "indexes"):
            options[key] = [item for item in options.get(key, ()) if not _uses(item, removed)]
        # The kind moves to the child, so the parent and its history stop declaring it.
        for operation in _kind_retirements(state, app_label):
            operation.state_forwards(app_label, state)
        state.remove_model(app_label, "markdownpage")
        state.add_model(
            ModelState(app_label, old.name, fields, options=options, bases=("knowledge.page",), managers=old.managers)
        )

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        old_model = from_state.apps.get_model(app_label, "MarkdownPage")
        page_model = from_state.apps.get_model(app_label, "Page")
        child_model = to_state.apps.get_model(app_label, "MarkdownPage")
        table = old_model._meta.db_table
        staging = f"{table}_child"
        quote = schema_editor.quote_name
        page_has_kind = any(field.name == "kind" for field in page_model._meta.local_concrete_fields)
        if page_has_kind:
            # A page without markdown would become a folder; only folders may lack it.
            alias = schema_editor.connection.alias
            linked = old_model._base_manager.using(alias).values("page_id")
            if page_model._base_manager.using(alias).exclude(kind="folder").exclude(pk__in=linked).exists():
                raise ValueError("A note or template page has no markdown row; give it one before migrating.")
        child_model._meta.db_table = staging
        try:
            schema_editor.create_model(child_model)
            columns, values = [], []
            for field in child_model._meta.local_concrete_fields:
                columns.append(quote(field.column))
                if field.name == "page_ptr":
                    values.append(f"old.{quote(old_model._meta.get_field('page').column)}")
                elif field.name == "kind" and page_has_kind:
                    values.append(f"page.{quote(page_model._meta.get_field('kind').column)}")
                elif field.name == "kind":
                    values.append("'note'")
                else:
                    values.append(f"old.{quote(field.column)}")
            schema_editor.execute(
                f"INSERT INTO {quote(staging)} ({', '.join(columns)}) "
                f"SELECT {', '.join(values)} FROM {quote(table)} old "
                f"JOIN {quote(page_model._meta.db_table)} page "
                f"ON page.{quote(page_model._meta.pk.column)} = old.{quote(old_model._meta.get_field('page').column)}"
            )
            schema_editor.delete_model(old_model)
            state = from_state
            for operation in _kind_retirements(from_state, app_label):
                after = state.clone()
                operation.state_forwards(app_label, after)
                operation.database_forwards(app_label, schema_editor, state, after)
                state = after
            schema_editor.alter_db_table(child_model, staging, table)
        finally:
            child_model._meta.db_table = table

    def describe(self):
        return "Rebuild MarkdownPage as a Page child"


class Migration(migrations.Migration):
    dependencies = [("knowledge", "__latest__")]
    operations = [RebuildMarkdownPageAsChild()]
