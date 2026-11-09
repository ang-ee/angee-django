"""Evaluate the live retrieval arms as a named actor, with no index mutation."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError, CommandParser
from rebac import actor_context, system_context

from angee.knowledge_graph_pgvector.evaluation import GoldenSet, evaluate
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend


class Command(BaseCommand):
    help = "Report recall@10 and MRR for vector, lexical, hybrid, hybrid+G0 and hybrid+G1."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "golden_set", type=Path, help="YAML/JSON with cases: query, expected_pages (titles), category."
        )
        parser.add_argument("--actor", required=True, help="Active user's login (the user model's USERNAME_FIELD).")
        parser.add_argument(
            "--vault", required=True, action="append", help="Readable pgvector vault public ID; repeat for more vaults."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            golden = GoldenSet.read(options["golden_set"])
            user_model = get_user_model()
            with system_context(reason="knowledge_graph_pgvector.evaluation.actor"):
                actor = user_model.objects.get_by_natural_key(options["actor"])
                if not actor.is_active:
                    raise ValueError("The evaluation actor must be active.")
            with actor_context(actor):
                rows = apps.get_model("knowledge", "Vault").objects.untrashed().filter(
                    retrieval_class=PgvectorRetrievalBackend.key,
                )
                vaults = [rows.from_public_id(public_id) for public_id in dict.fromkeys(options["vault"])]
                if any(vault is None for vault in vaults):
                    raise ValueError("Every selected vault must be readable, untrashed and use pgvector.")
                result = evaluate(golden, vaults)
        except (OSError, ValueError, ObjectDoesNotExist, yaml.YAMLError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(asdict(result), sort_keys=True, indent=2))
