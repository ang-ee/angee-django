"""PM demo loader contracts on emitted models, run by composed_host only."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import system_context


class PMDemoTests(TransactionTestCase):
    """Exercise PM's declared demo closure through the production ORM loader."""

    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.User = get_user_model()
        self.Task = apps.get_model("projects", "Task")
        self.Queue = apps.get_model("work", "Queue")
        self.Person = apps.get_model("parties", "Person")

    def test_demo_loads_adopts_people_and_seats_and_preserves_allocated_task_ranks(self):
        Resource = apps.get_model("resources", "Resource")
        Membership = apps.get_model("spaces", "Membership")
        Review = apps.get_model("proposals", "Review")
        with system_context(reason="tests.pm.demo.preexisting_person"):
            alice = self.User.objects.create_user(username="alice", password=None)
            existing = self.Person.objects.create(user=alice, display_name="Old name")
        loaded = Resource.objects.load_addons(
            apps.get_app_configs(), tiers=("master", "install", "demo"), allow_non_dev=True
        )
        self.assertGreater(loaded.loaded, 0)
        with system_context(reason="tests.pm.demo.assert"):
            queue = self.Queue.objects.get(slug="engineering")
            existing.refresh_from_db()
            self.assertEqual(existing.display_name, "Alice Morgan")
            self.assertEqual(self.Person.objects.filter(user=alice).count(), 1)
            seats = Membership.objects.filter(group=queue, party__person__user__username__in=("alice", "bob"))
            self.assertEqual(
                set(seats.values_list("party__person__user__username", "role", "is_confirmed")),
                {("alice", "owner", True), ("bob", "member", True)},
            )
            seat_ids = set(seats.values_list("pk", flat=True))
            # Forget the ledger so replay must adopt the seats by their natural key.
            Resource.objects.filter(source_addon="angee.pm", xref__in=("alice_engineering", "bob_engineering")).delete()
            self.assertEqual(
                set(self.Task.objects.filter(queue=queue).values_list("stage__category", flat=True)),
                {"triage", "backlog", "unstarted", "started", "completed"},
            )
            done = self.Task.objects.get(title="Confirm shared tool storage")
            self.assertEqual(done.status, "done")
            self.assertIsNotNone(done.done_at)
            reviews = list(Review.objects.select_related("proposal", "reviewer"))
            self.assertTrue(reviews)
            for review in reviews:
                self.assertTrue(review.proposal.with_actor(review.reviewer).has_access("evaluate"))
            # Model a stack with parentless tasks created after the original demo.
            later = self.Task.objects.create(
                title="Later stack task", queue=queue, sort_order=32768.0, sub_sort_order=32768.0,
            )
            Resource.objects.filter(
                source_addon="angee.work", xref__in=("eng_prepare_orientation", "eng_confirm_tool_storage"),
            ).delete()
            self.Task.objects.filter(
                title__in=("Prepare the volunteer orientation checklist", "Confirm shared tool storage"),
            ).delete()
        Resource.objects.load_addons(apps.get_app_configs(), tiers=("master", "install", "demo"), allow_non_dev=True)
        with system_context(reason="tests.pm.demo.replay"):
            self.assertEqual(set(seats.values_list("pk", flat=True)), seat_ids)
            self.assertEqual(Review.objects.count(), len(reviews))
            tasks = self.Task.objects.filter(
                title__in=("Prepare the volunteer orientation checklist", "Confirm shared tool storage"),
            ).order_by("sub_sort_order")
            ranks = list(tasks.values_list("pk", "sort_order", "sub_sort_order"))
            self.assertEqual(len(ranks), 2)
            self.assertEqual(len({rank[2] for rank in ranks}), 2)
            self.assertTrue(all(rank[2] > later.sub_sort_order for rank in ranks))
        # Force ORM replay rather than a hash skip, preserving ledger identities.
        with system_context(reason="tests.pm.demo.force_replay"):
            Resource.objects.filter(
                source_addon="angee.work", xref__in=("eng_prepare_orientation", "eng_confirm_tool_storage"),
            ).update(content_hash="")
        Resource.objects.load_addons(apps.get_app_configs(), tiers=("master", "install", "demo"), allow_non_dev=True)
        with system_context(reason="tests.pm.demo.rank_replay"):
            self.assertEqual(list(tasks.values_list("pk", "sort_order", "sub_sort_order")), ranks)
