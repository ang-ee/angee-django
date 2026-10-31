"""Native Work factories, selected-set writes and routed read contracts."""

from datetime import date, timedelta
from pathlib import Path

from django.test import override_settings
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class DomainOwnersTests(WorkCase):
    def test_capture_requires_explicit_trust_and_preserves_ownerless_triage(self):
        with actor_context(self.owner), self.assertRaises(PermissionDenied):
            self.Task.objects.capture_triage(queue=self.queue, title="Captured", note="")
        with system_context(reason="tests.work.capture"):
            task = self.Task.objects.capture_triage(
                queue=self.queue, title="  Captured   need  ", note="Evidence", created_by_id=self.owner.pk,
            )
        self.assertEqual((task.title, task.stage.category, task.status), ("Captured need", "triage", "open"))
        self.assertIsNone(task.owner_id)
        self.assertEqual(task.created_by_id, self.owner.pk)
        self.assertIsNotNone(task.number)
        self.assertTrue(hasattr(self.Task.objects, "schedule"))
        self.assertEqual(self.Task._base_manager.name, "_angee_base")

    def test_wake_preserves_selected_population(self):
        now = timezone.now()
        first = self.task(snoozed_until=now - timedelta(hours=1), snoozed_by=self.owner)
        second = self.task(snoozed_until=now - timedelta(hours=1), snoozed_by=self.owner)
        with system_context(reason="tests.work.wake"):
            changed = self.Task.objects.filter(pk=first.pk).wake_due_snoozes(now=now)
        self.assertEqual(changed, 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.snoozed_until)
        self.assertIsNotNone(second.snoozed_until)

    def test_cycle_maintenance_reads_the_write_database(self):
        class RejectUnboundDomainRead:
            def db_for_read(router_self, model, **hints):
                if model._meta.app_label in {"work", "projects"}:
                    instance = hints.get("instance")
                    if instance is not None and instance._state.db is not None:
                        return instance._state.db
                    raise AssertionError(f"Unbound domain read: {model._meta.label}")
                return None

            def db_for_write(router_self, model, **hints):
                return "default"

        with system_context(reason="tests.work.cadence"):
            self.queue.cycles_enabled = True
            self.queue.save(update_fields=("cycles_enabled", "updated_at"))
            with override_settings(DATABASE_ROUTERS=[RejectUnboundDomainRead()]):
                cycles = self.Cycle.objects.generate_for_queue(
                    self.queue, as_of=date(2026, 1, 1), window_end=date(2026, 2, 1),
                )
                self.assertGreaterEqual(len(cycles), 2)
                cycles[0].close(next_cycle=cycles[1])


def test_native_work_domain_owners(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_domain_refactor_work.DomainOwnersTests", app="angee.work")
