"""A GraphQL-only host has native CRUD without installing provenance."""

from __future__ import annotations

from pathlib import Path

from tests.composed_host import run_composed_tests


def test_graphql_native_crud_does_not_load_optional_provenance(tmp_path: Path) -> None:
    addon = tmp_path / "addons" / "native_graphql"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    (addon / "addon.toml").write_text(
        '[addon]\nname="native_graphql"\ndepends_on=["angee.graphql", "django.contrib.auth"]\n'
    )
    (addon / "models.py").write_text('''from django.db import models
from angee.base.models import AngeeDataModel

class Widget(AngeeDataModel):
    runtime = True
    sqid_prefix = "ngq_"
    title = models.CharField(max_length=80)
    class Meta:
        abstract = True
''')
    (addon / "schema.py").write_text('''from django.apps import apps
import strawberry
import strawberry_django
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode

Widget = apps.get_model("native_graphql", "Widget")
@strawberry_django.type(Widget)
class WidgetType(AngeeNode):
    title: strawberry.auto

resource = hasura_model_resource(
    WidgetType, model=Widget, name="native_widgets", filterable=["id", "title"],
    sortable=["title"], aggregatable=["id"], writable=["title"],
)
schemas = {"public": {"query": [resource.query], "mutation": [resource.mutation],
                     "types": [WidgetType, *resource.types]}}
''')
    (addon / "tests.py").write_text('''import sys
from types import SimpleNamespace
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase
from rebac import system_context
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.views import AngeeGraphQLView

class NativeCrudTests(TransactionTestCase):
    def test_native_crud_and_unsupported_claim(self):
        self.assertFalse(apps.is_installed("angee.provenance"))
        schema = GraphQLSchemas.from_discovery().build("public")
        context = SimpleNamespace(request=SimpleNamespace(user=AnonymousUser()))
        def execute(query, variables=None):
            with system_context(reason="native_graphql.crud"):
                result = schema.execute_sync(query, variable_values=variables or {}, context_value=context)
            self.assertIsNone(result.errors, result.errors)
            return result.data
        created = execute('mutation { insert_native_widgets_one(object:{title:"First"}) {id title} }')
        row = created["insert_native_widgets_one"]
        self.assertEqual(row["title"], "First")
        updated = execute(
            'mutation($id:String!) { update_native_widgets_by_pk('
            'pk_columns:{id:$id}, _set:{title:"Second"}) {title} }', {"id":row["id"]})
        self.assertEqual(updated["update_native_widgets_by_pk"]["title"], "Second")
        self.assertEqual(execute('{native_widgets {title}}')["native_widgets"], [{"title":"Second"}])
        refused = schema.execute_sync(
            'mutation { insert_native_widgets_one(object:{title:"Unsupported"},'
            'idempotency_key:"key") {id} }', context_value=context)
        self.assertTrue(refused.errors)
        self.assertIn("Unknown argument", refused.errors[0].message)
        execute('mutation($id:String!) {delete_native_widgets_by_pk(id:$id) {id}}', {"id":row["id"]})
        self.assertEqual(execute('{native_widgets {title}}')["native_widgets"], [])
        self.assertFalse(any(name == "angee.provenance" or name.startswith("angee.provenance.")
                             for name in sys.modules))
''')
    run_composed_tests(
        tmp_path, "native_graphql.tests", app="native_graphql", addon_dirs=(addon.parent,),
    )
