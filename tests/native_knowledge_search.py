"""Grouped searches preserve vault authority, backend ranking and bounded work."""

from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from fastmcp import FastMCP
from rebac import actor_context, system_context

from angee.knowledge.mcp_tools import register
from angee.knowledge.retrieval import LexicalRetrievalBackend
from tests.knowledge_retrieval import ReverseRetrieval

Vault = apps.get_model("knowledge", "Vault")
Page = apps.get_model("knowledge", "Page")


class SearchVaultTests(TransactionTestCase):
    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="knowledge search participants"):
            self.owner = get_user_model().objects.create_user(username="search-owner")
            self.reader = get_user_model().objects.create_user(username="search-reader")
        with actor_context(self.owner):
            self.first = Vault.objects.create_for(self.owner, name="A lexical")
            self.second = Vault.objects.create_for(self.owner, name="B lexical")
            self.third = Vault.objects.create_for(self.owner, name="C reverse", retrieval_class="reverse")
            self.hidden = Vault.objects.create_for(self.owner, name="D hidden")
            self.a = Page.objects.create_in(self.first, title="Needle A")
            self.b = Page.objects.create_in(self.second, title="Needle B")
            self.c = Page.objects.create_in(self.third, title="Needle C")
            self.d = Page.objects.create_in(self.third, title="Needle D")
            self.private = Page.objects.create_in(self.hidden, title="Needle private")
            for vault in (self.first, self.second, self.third):
                vault.grant_record_access("viewer", self.reader)

    def test_total_budget_and_backend_ranking(self):
        with actor_context(self.reader):
            self.assertEqual([row.pk for row in Vault.objects.search_pages("Needle", first=3)],
                             [self.a.pk, self.b.pk, self.d.pk])
            self.assertEqual([row.pk for row in Vault.objects.search_pages("Needle", first=1000)],
                             [self.a.pk, self.b.pk, self.d.pk, self.c.pk])
            self.assertEqual(Vault.objects.search_pages("Needle", first=0), [])
            with patch.object(ReverseRetrieval, "search_many", side_effect=AssertionError("Budget already full")):
                self.assertEqual(len(Vault.objects.search_pages("Needle", first=2)), 2)

    def test_two_lexical_vaults_use_one_page_query(self):
        with actor_context(self.reader), CaptureQueriesContext(connection) as captured:
            rows = Vault.objects.filter(pk__in=(self.first.pk, self.second.pk)).search_pages("Needle")
        self.assertEqual([row.pk for row in rows], [self.a.pk, self.b.pk])
        # Native hierarchy authorization may read page identities; only the
        # text search should scan each vault group once.
        page_queries = [item["sql"] for item in captured
                        if f'FROM "{Page._meta.db_table}"' in item["sql"] and " LIKE " in item["sql"]]
        self.assertEqual(len(page_queries), 1, page_queries)

    def test_vault_work_cap_applies_even_without_matches(self):
        with actor_context(self.reader), patch("angee.knowledge.models.MAX_SEARCH_VAULTS", 2), \
                patch.object(LexicalRetrievalBackend, "search_many",
                             wraps=LexicalRetrievalBackend.search_many) as lexical, \
                patch.object(ReverseRetrieval, "search_many", side_effect=AssertionError("Vault cap exceeded")):
            self.assertEqual(Vault.objects.search_pages("absent"), [])
        self.assertEqual(lexical.call_count, 1)
        self.assertEqual({vault.pk for vault in lexical.call_args.args[0]}, {self.first.pk, self.second.pk})

    def test_hidden_vault_and_trashed_pages_stay_out(self):
        with actor_context(self.owner):
            self.a.trash()
            self.private.grant_record_access("viewer", self.reader)
        with actor_context(self.reader):
            self.assertEqual([row.pk for row in Vault.objects.search_pages("Needle")],
                             [self.b.pk, self.d.pk, self.c.pk])

    def test_tools_list_readable_vaults_and_execute_global_and_single_vault_searches(self):
        server = FastMCP(name="knowledge-search")
        register(server)
        tools = {tool.name: tool for tool in async_to_sync(server.list_tools)()}
        self.assertEqual(tools["search_pages"].parameters["required"], ["query"])
        with actor_context(self.reader):
            result = async_to_sync(tools["list_vaults"].run)({})
            self.assertEqual({row["sqid"] for row in result.structured_content["result"]},
                             {self.first.sqid, self.second.sqid, self.third.sqid})
            result = async_to_sync(tools["search_pages"].run)({"query": "Needle", "first": 3})
            self.assertEqual([row["sqid"] for row in result.structured_content["result"]],
                             [self.a.sqid, self.b.sqid, self.d.sqid])
            result = async_to_sync(tools["search_pages"].run)({"vault": self.third.sqid, "query": "Needle"})
            self.assertEqual([row["sqid"] for row in result.structured_content["result"]], [self.d.sqid, self.c.sqid])
