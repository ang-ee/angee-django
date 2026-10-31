"""Intake request and access transitions share their producer database."""

from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.test import TransactionTestCase
from rebac import actor_context, database_context, system_context
from rebac.roles import grant as grant_role

from angee.base.testing.packages import write_addon_distribution
from angee.intake.choices import NeedAccessAction
from angee.provenance.context import changeset
from tests.composed_host import run_composed_tests


class IntakeRouter:
    def db_for_read(self, model, **hints):
        return "producer"

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "producer"


class IntakeDatabaseContract(TransactionTestCase):
    __test__ = False
    databases = {"default", "producer"}

    def test_request_replay_access_reset_and_rollback_share_the_producer(self):
        call_command("rebac", "sync", database="producer", verbosity=0)
        Need = apps.get_model("intake", "Need")
        Task = apps.get_model("projects", "Task")
        Decision = apps.get_model("decisions", "Decision")
        with (
            database_context("producer"),
            system_context(reason="tests.intake.routing.seed"),
            changeset(fresh=True, using="producer"),
        ):
            owner = get_user_model().objects.db_manager("producer").create_user(username="intake-producer")
            grant_role(actor=owner, role="angee/role:admin")
            queue = apps.get_model("work", "Queue").objects.db_manager("producer").personal_for(owner, provision=True)
            party = apps.get_model("parties", "Party").objects.db_manager("producer").for_user(owner)
        values = {
            "queue": queue,
            "party": party,
            "title": "Producer request",
            "body": "Preserve the request transaction",
            "idempotency_key": "intake-producer-request",
        }
        with database_context("producer"), actor_context(owner):
            baseline = Decision._base_manager.using("producer").count()
            with self.assertRaisesRegex(ValueError, "rollback request"):
                with transaction.atomic(using="producer"):
                    with transaction.atomic(using="default"):
                        task = Need.objects.db_manager("producer").file_task(**values)
                        self.assertEqual(task._state.db, "producer")
                    raise ValueError("rollback request")
            self.assertFalse(Need._base_manager.using("producer").exists())
            self.assertFalse(Task._base_manager.using("producer").filter(title=values["title"]).exists())
            self.assertEqual(Decision._base_manager.using("producer").count(), baseline)
            task = Need.objects.db_manager("producer").file_task(**values)
            replay = Need.objects.db_manager("producer").file_task(**values)
            self.assertEqual(replay.pk, task.pk)
            need = Need.objects.db_manager("producer").get(task=task)
            self.assertEqual(need._state.db, "producer")
            self.assertEqual(need.target._state.db, "producer")
            self.assertEqual(Need._base_manager.using("producer").count(), 1)
            with changeset(fresh=True, using="producer"):
                question = need.access_decision_id
                need.reset_access(confirmed=True, expected_revision=need.revision)
            self.assertNotEqual(need.access_decision_id, question)
            old = Decision.objects.db_manager("producer").get(pk=question)
            self.assertFalse(old.is_open)
            with changeset(fresh=True, using="producer"):
                need.decide_access(NeedAccessAction.INTAKE_APPROVE, expected_revision=need.revision)
            need.refresh_from_db(using="producer")
            self.assertEqual(need.admitted_user_id, owner.pk)
            self.assertEqual(need.access_decision._state.db, "producer")


def test_intake_database_contract(tmp_path: Path):
    addon = tmp_path / "addons" / "example" / "routed_intake"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon, "example.routed_intake", depends_on=("angee.intake",))
    (addon / "autoconfig.py").write_text(
        'SETTINGS = {"DATABASE_ROUTERS": ["tests.test_intake_database.IntakeRouter"], '
        '"DATABASES.producer": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}\n'
    )
    run_composed_tests(
        tmp_path,
        "tests.test_intake_database.IntakeDatabaseContract",
        app="example.routed_intake",
        addon_dirs=(tmp_path / "addons",),
    )
