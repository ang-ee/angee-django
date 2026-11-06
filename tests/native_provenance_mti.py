"""Canonical protected MTI contracts on the real emitted history model graph."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rebac import actor_context, system_context, to_subject_ref

from angee.provenance.context import changeset, current_changeset


class CanonicalProtectedHistoryMTITests(TransactionTestCase):
    __test__ = False

    def setUp(self):
        self.enterContext(override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry"))
        call_command("rebac", "sync", verbosity=0)
        self.owner = get_user_model().objects.create_person_as_system(
            username="mti-history-owner", email="mti-history-owner@example.test", reason="tests.provenance.mti.owner",
        )
        self.Parent = apps.get_model("mti_history", "Parent")
        self.Child = apps.get_model("mti_history", "Child")

    def test_canonical_child_preserves_parent_identity_managers_and_each_capture_path(self):
        Parent, Child = self.Parent, self.Child
        self.assertEqual(Parent._base_manager.name, "unscoped_objects")
        self.assertEqual(Child._base_manager.name, "_angee_base")
        self.assertEqual(Parent._default_manager.name, "objects")
        self.assertEqual(Child._default_manager.name, "objects")
        self.assertEqual(Parent.objects.factory_marker(), "parent")
        self.assertEqual(Child.objects.factory_marker(), "child")
        self.assertIs(Child._meta.get_ancestor_link(Parent), Child._meta.get_field("parent_ptr"))
        self.assertEqual({field.name for field in Child._meta.local_fields}, {"parent_ptr", "child_key", "detail"})
        self.assertEqual(
            {model._meta.model_name for model in apps.get_app_config("mti_history").get_models()},
            {"parent", "child", "historicalparent", "historicalchild"},
        )
        expected_tables = {
            model._meta.db_table for model in (Parent, Child, Parent.history.model, Child.history.model)
        }
        self.assertEqual(len(expected_tables), 4)
        self.assertTrue(expected_tables <= set(connection.introspection.table_names()))
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, Child._meta.db_table)
            self.assertEqual(
                {column.name for column in columns},
                {"parent_ptr_id", "child_key", "detail"},
            )

        with actor_context(self.owner), system_context(reason="tests.provenance.mti.capture"):
            with changeset(fresh=True, change_reason="tests.provenance.mti.create"):
                row = Child.objects.create(
                    id=11, child_key=71, title="parent title", owner=self.owner, detail="created",
                )
                created = current_changeset(record=True)
            self.assertEqual((row.id, row.pk, row.parent_ptr_id), (11, 71, 11))
            self.assertEqual(Parent.objects.named("parent title").get().pk, 11)
            self.assertEqual(Child.objects.get(pk=71).parent_ptr.pk, 11)
            self.assertEqual(Parent.objects.count(), 1)
            self.assertEqual(Child.objects.count(), 1)

            with changeset(fresh=True, change_reason="tests.provenance.mti.instance"):
                row.detail = "instance"
                row.save(update_fields=["detail"])
                instance = current_changeset(record=True)
            with changeset(fresh=True, change_reason="tests.provenance.mti.queryset"):
                self.assertEqual(Child._base_manager.filter(pk=71).update(detail="base queryset"), 1)
                queryset = current_changeset(record=True)
            with changeset(fresh=True, change_reason="tests.provenance.mti.bulk"):
                row.refresh_from_db()
                row.detail = "bulk"
                self.assertEqual(Child._base_manager.bulk_update([row], ["detail"]), 1)
                bulk = current_changeset(record=True)

            for receipt, value in (
                (created, "created"), (instance, "instance"), (queryset, "base queryset"), (bulk, "bulk"),
            ):
                snapshot = Child.history.get(history_changeset=receipt)
                self.assertEqual((snapshot.id, snapshot.child_key, snapshot.parent_ptr_id), (11, 71, 11))
                self.assertEqual(snapshot.detail, value)
                self.assertEqual(snapshot.title, "parent title")
                self.assertEqual(snapshot.history_user_id, self.owner.pk)
                self.assertEqual(snapshot.history_changeset.actor_ref, str(to_subject_ref(self.owner)))
                self.assertTrue(snapshot.history_source_complete)
            self.assertEqual(Child.history.filter(child_key=71).count(), 4)
            self.assertEqual(Parent.objects.get(pk=11).title, "parent title")

            with changeset(fresh=True, change_reason="tests.provenance.mti.parent.bulk"):
                [other] = Parent._base_manager.bulk_create([Parent(id=31, title="bulk parent", owner=self.owner)])
                parent_bulk = current_changeset(record=True)
            snapshot = Parent.history.get(history_changeset=parent_bulk)
            self.assertEqual((snapshot.id, snapshot.title), (other.pk, "bulk parent"))
            self.assertEqual(snapshot.history_user_id, self.owner.pk)
            self.assertTrue(snapshot.history_source_complete)
            self.assertEqual(Child.objects.count(), 1)
