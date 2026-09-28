"""Convert legacy invitation and ceremony grants into shells and receipts.

Both local stores are visited regardless of the active backend setting. Historical
models bypass today's admission lifecycle so terminal rounds retain their roster.
Only unconditional named grants create rows. Group invitations snapshot named
members; unresolved requester identities are logged and never guessed. Reversal
does not reconstruct obsolete grants from subsequently edited domain rows.
Tuple rows have no dependents; raw deletes avoid invoking current application
signals against historical model states.
"""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import migrations, models, router
from django.db.migrations.state import ProjectState

logger = logging.getLogger(__name__)
UNCONDITIONAL = models.Q(expires_at__isnull=True, caveat_name="")


def applies(state: ProjectState) -> bool:
    proposal = state.models.get(("proposals", "proposal"))
    return (
        proposal is not None
        and "disclosed_at" in proposal.fields
        and all(
            key in state.models
            for key in (
                ("proposals", "round"),
                ("parties", "person"),
                ("rebac", "relationship"),
                ("rebac", "relationshipregistry"),
            )
        )
    )


def named_users(stores, kind, identifier, relation):
    """Expand unconditional direct named members as the roster conversion does."""
    if kind == "auth/user" and identifier != "*" and not relation:
        yield identifier
    elif kind == "auth/group" and relation == "member":
        for rows, resource_type, resource_id, subject_type, subject_id in stores:
            yield from (
                rows.filter(
                    UNCONDITIONAL,
                    **{
                        resource_type: "auth/group",
                        resource_id: identifier,
                        "relation": "member",
                        subject_type: "auth/user",
                        "optional_subject_relation": "",
                    },
                )
                .exclude(**{subject_id: "*"})
                .values_list(subject_id, flat=True)
                .iterator(chunk_size=500)
            )


def transition(apps, alias, *, apply=False):
    """Stream the same historical operations for application and read-only preview."""
    round_model = apps.get_model("proposals", "Round")
    proposal_model = apps.get_model("proposals", "Proposal")
    person_model = apps.get_model("parties", "Person")
    if not all(router.allow_migrate_model(alias, model) for model in (round_model, proposal_model, person_model)):
        return
    rounds = round_model._base_manager.using(alias).order_by()
    proposals = proposal_model._base_manager.using(alias).order_by()
    people = person_model._base_manager.using(alias).order_by()
    users = apps.get_model(settings.AUTH_USER_MODEL)._base_manager.using(alias).order_by()
    stores = []
    for name, resource_type, resource_id, subject_type, subject_id in (
        ("Relationship", "resource_type", "resource_id", "subject_type", "subject_id"),
        (
            "RelationshipRegistry",
            "resource_fk__resource_type",
            "resource_fk__resource_id",
            "subject_fk__resource_type",
            "subject_fk__resource_id",
        ),
    ):
        model = apps.get_model("rebac", name)
        if router.allow_migrate_model(alias, model):
            stores.append(
                (model._base_manager.using(alias).order_by(), resource_type, resource_id, subject_type, subject_id)
            )

    for round in rounds.iterator(chunk_size=500):
        shells = proposals.filter(round_id=round.pk)
        holders = {str(value) for value in shells.exclude(responder_id=None).values_list("responder_id", flat=True)}
        requester_ids = set()
        for rows, resource_type, resource_id, subject_type, subject_id in stores:
            invitations = rows.filter(
                **{
                    resource_type: "proposals/round",
                    resource_id: str(round.pk),
                    "relation__in": ("responder", "requester"),
                }
            )
            for pk, relation, kind, identifier, subject_relation, caveat, expiry in invitations.values_list(
                "pk",
                "relation",
                subject_type,
                subject_id,
                "optional_subject_relation",
                "caveat_name",
                "expires_at",
            ).iterator(chunk_size=500):
                found = False
                if not caveat and expiry is None:
                    for user_id in named_users(stores, kind, identifier, subject_relation):
                        try:
                            user = users.filter(pk=user_id).first()
                        except ValidationError, ValueError, TypeError:
                            user = None
                        if user is None:
                            continue
                        found = True
                        if relation == "requester":
                            requester_ids.add(user.pk)
                        elif str(user.pk) not in holders:
                            yield ("create shell", f"proposals/round:{round.pk}", str(user.pk))
                            if apply:
                                proposals.get_or_create(round_id=round.pk, responder_id=user.pk)
                            holders.add(str(user.pk))
                if not found:
                    logger.warning(
                        "Unresolved proposal invitation round=%s tuple=%s subject=%s:%s#%s",
                        round.pk,
                        pk,
                        kind,
                        identifier,
                        subject_relation,
                    )
                yield (
                    "delete legacy invitation",
                    f"proposals/round:{round.pk}",
                    f"{kind}:{identifier}#{subject_relation}",
                )
                if apply:
                    rows.filter(pk=pk)._raw_delete(alias)

        requester = people.filter(pk=round.requester_party_id).values_list("user_id", flat=True).first()
        unresolved = requester_ids - ({requester} if requester is not None else set())
        if unresolved and (len(requester_ids) != 1 or round.requester_party_id is not None):
            logger.warning(
                "Unresolved proposal requesters round=%s users=%s existing_party=%s",
                round.pk,
                sorted(str(value) for value in requester_ids),
                round.requester_party_id,
            )
            yield ("unresolved requester", f"proposals/round:{round.pk}", ",".join(sorted(map(str, requester_ids))))
        elif unresolved:
            user = users.get(pk=next(iter(requester_ids)))
            yield ("identify requester", f"proposals/round:{round.pk}", str(user.pk))
            if apply:
                person, _ = people.get_or_create(
                    user_id=user.pk,
                    defaults={
                        "display_name": f"{user.first_name} {user.last_name}".strip() or user.username,
                        "created_by_id": user.pk,
                    },
                )
                rounds.filter(pk=round.pk, requester_party_id=None).update(requester_party_id=person.pk)

        disclosed = {
            str(value)
            for value in shells.filter(disclosed_at__isnull=False)
            .exclude(responder_id=None)
            .values_list("responder_id", flat=True)
        }
        submitted = {
            str(value)
            for value in shells.filter(submitted_at__isnull=False)
            .exclude(responder_id=None)
            .values_list("responder_id", flat=True)
        }
        for proposal in shells.iterator(chunk_size=500):
            for rows, resource_type, resource_id, subject_type, subject_id in stores:
                common = {subject_type: "auth/user", "optional_subject_relation": ""}
                owned = models.Q(relation="editor", **{subject_id: str(proposal.responder_id)})
                if proposal.disclosed_at is not None:
                    owned |= models.Q(relation="reader", **{f"{subject_id}__in": disclosed})
                ceremony = rows.filter(
                    owned,
                    **common,
                    **{
                        resource_type: "proposals/proposal",
                        resource_id: str(proposal.pk),
                    },
                )
                for pk, holder in ceremony.values_list("pk", subject_id).iterator(chunk_size=500):
                    yield ("delete ceremony share", f"proposals/proposal:{proposal.pk}", holder)
                    if apply:
                        rows.filter(pk=pk)._raw_delete(alias)
                if proposal.track_id is None:
                    continue
                shares = rows.filter(
                    **common, **{resource_type: "projects/project", resource_id: str(proposal.track_id)}
                )
                published = shares.filter(relation="reader", **{f"{subject_id}__in": holders}).exists()
                if published and proposal.track_published_at is None:
                    yield ("stamp track publication", f"proposals/proposal:{proposal.pk}", "")
                    if apply:
                        stamp = round.opened_at or round.updated_at
                        proposals.filter(pk=proposal.pk, track_published_at=None).update(track_published_at=stamp)
                        proposal.track_published_at = stamp
                ceremony = shares.filter(
                    models.Q(relation="reader", **{f"{subject_id}__in": submitted})
                    | models.Q(
                        relation="editor",
                        **{f"{subject_id}__in": [str(round.facilitator_id), str(proposal.responder_id)]},
                    )
                )
                for pk, holder in ceremony.values_list("pk", subject_id).iterator(chunk_size=500):
                    yield ("delete ceremony share", f"projects/project:{proposal.track_id}", holder)
                    if apply:
                        rows.filter(pk=pk)._raw_delete(alias)


def forwards(apps, schema_editor):
    for _ in transition(apps, schema_editor.connection.alias, apply=True):
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("rebac", "__latest__"),
        ("parties", "__latest__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
