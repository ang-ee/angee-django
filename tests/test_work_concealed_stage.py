"""Concealment removes every non-administrator grant in both relationship stores."""

from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import Count
from rebac import PermissionDenied, actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class ConcealedStageTests(WorkCase):
    def test_held_instances_scopes_evidence_and_chatter_follow_concealment(self):
        task = self.task(assignee=self.assignee)
        self.share(task, self.reader)
        with system_context(reason="tests.work.need"):
            need = apps.get_model("intake", "Need").objects.create(
                task=task,
                party=self.Person._base_manager.get(user=self.reader),
                body="Request evidence",
            )
        with actor_context(self.reader):
            self.as_user(task, self.reader).message_subscribe()
        with actor_context(self.owner):
            message = self.as_user(task).message_post("Visible discussion")
            attachment = self.as_user(task).message_thread_attachment(create=False)
            held = self.as_user(task)
            held.stage = self.stages["Removed"]
            held.save(update_fields=("stage", "updated_at"))
        for user in (self.owner, self.assignee, self.reader, self.member, self.moderator, self.viewer):
            for permission in ("read", "write", "share", "comment", "delete"):
                with self.subTest(user=user.username, permission=permission):
                    self.assertFalse(self.as_user(task, user).has_access(permission))
                    self.assertFalse(self.scoped(task, user, permission))
            with actor_context(user):
                rows = self.Task.objects.filter(pk=task.pk)
                self.assertFalse(rows.exists())
                self.assertEqual(rows.aggregate(total=Count("pk")), {"total": 0})
                self.assertEqual(list(rows.values("stage_id").annotate(total=Count("pk"))), [])
                for row in (need, message, message.thread, attachment):
                    self.assertFalse(self.scoped(row, user))
            with actor_context(user), self.assertRaises(PermissionDenied):
                self.as_user(task, user).message_post("Refused comment")
            with actor_context(user), self.assertRaises(PermissionDenied):
                changed = self.as_user(task, user)
                changed.title = "Refused write"
                changed.save(update_fields=("title", "updated_at"))
            with actor_context(user), self.assertRaises(PermissionDenied):
                self.as_user(task, user).delete()
        for permission in ("read", "write", "share", "comment", "delete"):
            self.assertTrue(self.as_user(task, self.admin).has_access(permission))
            self.assertTrue(self.scoped(task, self.admin, permission))
        with actor_context(self.admin):
            hidden = self.as_user(task, self.admin)
            update = hidden.message_post("Administrative update")
            self.assertEqual(self.recipients(update), set())
            hidden.stage = self.stages["Ready"]
            hidden.save(update_fields=("stage", "updated_at"))
        for user in (self.owner, self.assignee, self.reader, self.member, self.moderator):
            self.assertTrue(self.scoped(task, user))

    def test_promoted_task_cannot_enter_concealment(self):
        task = self.task()
        with actor_context(self.owner):
            self.as_user(task).promote_to_project()
        with system_context(reason="tests.work.conceal_promoted"):
            promoted = self.Task._base_manager.get(pk=task.pk)
            promoted.stage = self.stages["Removed"]
            with self.assertRaises(ValidationError):
                promoted.save(update_fields=("stage", "updated_at"))
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages["Active"].pk)

    def test_public_reader_loses_concealed_task(self):
        """Requires public queue creation from the spaces filtered-constant change."""
        with system_context(reason="tests.work.public_concealment"):
            queue = self.Queue.objects.create(
                name="Public requests",
                key="PUB",
                owner=self.manager,
                visibility="public",
            )
            removed = self.Stage.objects.create(queue=queue, name="Removed", category="canceled", conceals=True)
        task = self.task(queue=queue, stage=None)
        self.assertTrue(self.scoped(task, self.outsider))
        with actor_context(self.owner):
            task = self.as_user(task)
            task.stage = removed
            task.save(update_fields=("stage", "updated_at"))
        self.assertFalse(self.scoped(task, self.outsider))
        self.assertTrue(self.scoped(task, self.admin))


class ConcealedDenormalizedTests(ConcealedStageTests):
    storage = "denormalized"


@pytest.mark.parametrize("test_class", ["ConcealedStageTests", "ConcealedDenormalizedTests"])
def test_work_concealed_stage(tmp_path: Path, test_class: str):
    run_composed_tests(tmp_path, f"tests.test_work_concealed_stage.{test_class}", app="angee.intake")
