"""Bulk creation retains native receivers, source admission and record watches."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from rebac import actor_context, to_subject_ref

from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepWatch, Trigger, TriggerEvent
from tests.conftest import Page, Vault
from tests.test_workflows_triggers import trigger_resource_schema as trigger_resource_schema
from tests.test_workflows_watches import Watch, record_deliveries, start_watcher
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def test_clone_native_batch_delivers_all_receivers_triggers_and_new_watches(
    execution, trigger_resource_schema, register_step,
):
    """An earlier native receiver can register watches before final batch capture."""
    actor, sent = execution
    with actor_context(actor):
        source = Vault.objects.create_for(actor, name="Template")
        Page.objects.create_in(source, title="First", kind=Page.PageKind.FOLDER)
        Page.objects.create_in(source, title="Second", kind=Page.PageKind.FOLDER)
        waiting = Vault.objects.create_for(actor, name="Waiting")
    workflow = load_workflow(document("entry"), key="copy-source", actor=actor, subject_model="knowledge.page")
    with actor_context(actor):
        trigger = Trigger.objects.create(workflow=workflow, source="record_changed", model_label="knowledge.Page")
    Trigger.objects.enable(trigger, actor=actor)
    register_step(Watch)
    run, step = start_watcher(Watch, waiting, actor, key="copy-watch")
    run_until(run)
    sent.clear()
    observed = []

    def native_receiver(sender, instance, created, raw, created_batch=None, **kwargs):
        assert created and not raw and isinstance(created_batch, tuple)
        assert all(row.pk is not None and row._rebac_actor == to_subject_ref(actor) for row in created_batch)
        observed.append(instance.pk)
        if instance is created_batch[0]:
            assert not TriggerEvent.system_queryset().filter(trigger=trigger).exists()
            StepWatch.objects.register(step, created_batch, actor=actor)

    uid = "test.clone.native-batch"
    post_save.connect(native_receiver, sender=Page, weak=False, dispatch_uid=uid)
    try:
        with actor_context(actor):
            copy = Vault.objects.create_from(source, name="Observed copy")
    finally:
        post_save.disconnect(sender=Page, dispatch_uid=uid)
    copies = tuple(Page.objects.with_actor(actor).filter(vault=copy).order_by("pk"))
    copied_keys = {page.pk for page in copies}
    assert len(observed) == 2 and set(observed) == copied_keys
    events = TriggerEvent.system_queryset().filter(trigger=trigger)
    assert set(events.values_list("record_object_id", flat=True)) == copied_keys
    content_type = ContentType.objects.get_for_model(Page)
    assert StepWatch.system_queryset().filter(
        step_run=step, content_type=content_type, object_id__in=copied_keys, pending=True,
    ).count() == 2
    assert len(record_deliveries(sent)) == 2
    assert Page.history.filter(vault_id=copy.pk).count() == 2
