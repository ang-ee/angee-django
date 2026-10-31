"""Explicit native ownership for independent and combined project knowledge graphs."""

from pathlib import Path

import pytest
from django.apps import apps
from django.test import SimpleTestCase

from tests.composed_host import run_composed_tests


class ProjectsOnlyContract(SimpleTestCase):
    __test__ = False

    def test_project_owner_has_no_knowledge_contribution(self):
        project = apps.get_model("projects", "Project")
        self.assertFalse(apps.is_installed("angee.projects_knowledge"))
        self.assertFalse(any(base.__module__ == "angee.projects_knowledge.models" for base in project.__mro__))


class KnowledgeOnlyContract(SimpleTestCase):
    __test__ = False

    def test_knowledge_does_not_require_project_targets(self):
        self.assertIsNotNone(apps.get_model("knowledge", "Vault"))
        self.assertFalse(apps.is_installed("angee.projects_knowledge"))


class CombinedContract(SimpleTestCase):
    __test__ = False

    def test_combined_graph_adds_owned_setup_input_and_binding(self):
        from angee.graphql.schema import GraphQLSchemas

        project = apps.get_model("projects", "Project")
        vault = apps.get_model("knowledge", "Vault")
        self.assertTrue(any(base.__module__ == "angee.projects_knowledge.models" for base in project.__mro__))
        self.assertIsNotNone(vault._meta.get_field("project_bindings"))
        for bucket in ("public", "console"):
            schema = GraphQLSchemas.from_discovery().build(bucket)
            setup = schema._schema.get_type("ProjectSetupInput")
            self.assertIn("vault_template", setup.fields)


@pytest.mark.parametrize(
    ("app", "case"),
    [
        ("angee.projects", "ProjectsOnlyContract"),
        ("angee.knowledge", "KnowledgeOnlyContract"),
        ("angee.projects_knowledge", "CombinedContract"),
    ],
)
def test_explicit_project_knowledge_graphs(tmp_path: Path, app: str, case: str):
    run_composed_tests(tmp_path, f"tests.test_intelligence_refactor_composition.{case}", app=app)
