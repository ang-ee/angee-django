"""Shared fixtures for work contracts on the isolated, emitted model graph."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import backend
from rebac.backends.local_query import LocalQueryScope
from rebac.roles import grant


class WorkCase(TransactionTestCase):
    __test__ = False
    storage = "registry"

    def setUp(self):
        settings = override_settings(REBAC_LOCAL_BACKEND_STORAGE=self.storage, REBAC_SUPERUSER_BYPASS=False)
        settings.enable()
        self.addCleanup(settings.disable)
        call_command("rebac", "sync", verbosity=0)
        for app, names in (
            ("work", ("Queue", "Stage", "Cycle")),
            ("projects", ("Task", "Project", "Milestone", "Link")),
            ("spaces", ("Membership",)),
            ("parties", ("Person",)),
            ("messaging", ("ThreadFollower", "ThreadNotification")),
        ):
            for name in names:
                setattr(self, name, apps.get_model(app, name))
        for name in ("owner", "manager", "moderator", "member", "viewer", "assignee", "reader", "outsider", "admin"):
            setattr(self, name, self.person(name))
        with system_context(reason="tests.work.setup"):
            grant(actor=self.admin, role="angee/role:admin")
            self.queue = self.Queue.objects.create(
                name="Operations",
                key="OPS",
                owner=self.manager,
                provision_stages=False,
                triage_enabled=True,
            )
            self.stages = {}
            for position, (name, category, flags) in enumerate(
                (
                    ("Triage", "triage", {}),
                    ("Ready", "unstarted", {}),
                    ("Doing", "started", {}),
                    ("Completed", "completed", {}),
                    ("Declined", "canceled", {}),
                    ("Active", "started", {"rule_owned": True}),
                    ("Final", "completed", {"rule_owned": True}),
                    ("Canceled", "canceled", {"rule_owned": True}),
                    ("Removed", "canceled", {"conceals": True}),
                    ("Duplicate", "duplicate", {}),
                )
            ):
                self.stages[name] = self.Stage.objects.create(
                    queue=self.queue,
                    name=name,
                    category=category,
                    position=position,
                    **flags,
                )
        self.membership(self.member)
        self.membership(self.viewer, role="viewer")
        self.membership(self.moderator, role="moderator")

    def person(self, name):
        return get_user_model().objects.create_person_as_system(
            username=name,
            email=f"{name}@example.test",
            reason="tests.work.person",
        )

    def membership(self, user, *, queue=None, role="member", **fields):
        with system_context(reason="tests.work.membership"):
            return self.Membership.objects.create(
                group=(queue or self.queue).group_ptr,
                party=self.Person._base_manager.get(user=user),
                role=role,
                is_confirmed=True,
                **fields,
            )

    def task(self, stage="Ready", **fields):
        values = {"title": "Request", "queue": self.queue, "owner": self.owner, "stage": self.stages[stage]}
        values.update(fields)
        with system_context(reason="tests.work.task"):
            return self.Task.objects.create(**values).unsudo()

    def as_user(self, row, user=None):
        return type(row)._base_manager.get(pk=row.pk).with_actor(user or self.owner)

    def share(self, row, user, relation="reader"):
        with system_context(reason="tests.work.share"):
            write_relationships([RelationshipTuple(to_object_ref(row), relation, to_subject_ref(user))])

    def scoped(self, row, user, permission="read"):
        predicate = LocalQueryScope(backend(), to_subject_ref(user), "default").predicate(
            type(row),
            permission,
            row._meta.rebac_resource_type,
        )
        rows = type(row)._base_manager.filter(predicate, pk=row.pk)
        sql, _ = rows.query.sql_with_params()
        self.assertIn("SELECT", sql)
        return rows.exists()

    def recipients(self, message):
        return set(self.ThreadNotification._base_manager.filter(message=message).values_list("user_id", flat=True))
