"""PostgreSQL interleavings of tag edge writes with the category-side writes they race.

An edge write locks its record and then its tags (``no_key``) before it reads a
tag's category; a refiling or a lowered maximum takes the category's full row
lock and the same tag locks before it judges. Whichever runs second waits on the
other's locks and judges its committed state.
"""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from rebac import actor_context, system_context

from angee.base.models.identity import public_id_for
from angee.messaging.testing.models import Party
from angee.tags.testing.models import Tag, TagAssignment, TagCategory
from tests.conftest import create_user
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def _held(record: Any) -> set[str]:
    with system_context(reason="tags concurrency test read"):
        return set(TagAssignment.objects.for_record(record).values_list("tag__name", flat=True))


@pytest.fixture()
def vocabulary() -> SimpleNamespace:
    owner = create_user("tags-concurrency-owner")
    with system_context(reason="tags concurrency test vocabulary"):
        relationship = TagCategory.objects.create(name="Relationship", max_per_record=2)
        prospect = Tag.objects.create(name="Prospect", category=relationship)
        partner = Tag.objects.create(name="Partner", category=relationship)
        loose = Tag.objects.create(name="Loose")
    with actor_context(owner):
        party = Party.objects.create(display_name="Eta Ltd")
    address = ("parties/party", public_id_for(Party, party.pk))
    return SimpleNamespace(
        owner=owner,
        relationship=relationship,
        prospect=prospect,
        partner=partner,
        loose=loose,
        party=party,
        address=address,
    )


def test_a_lowered_maximum_waits_for_an_attach_in_flight_and_judges_it(vocabulary: SimpleNamespace) -> None:
    """The category save parks on the attached tags' locks, then sees both edges and refuses."""

    def lower_to_one() -> None:
        with system_context(reason="tags concurrency test narrow"):
            category = TagCategory.objects.get(pk=vocabulary.relationship.pk)
            category.max_per_record = 1
            category.save()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), actor_context(vocabulary.owner):
            TagAssignment.objects.attach(*vocabulary.address, [vocabulary.prospect.sqid, vocabulary.partner.sqid])
            contender, pid = submit(pool, lower_to_one)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError, match="Records already hold more than 1 tag from Relationship"):
            contender.result(timeout=10)
    with system_context(reason="tags concurrency test read"):
        assert TagCategory.objects.get(pk=vocabulary.relationship.pk).max_per_record == 2
    assert _held(vocabulary.party) == {"Prospect", "Partner"}


def test_an_attach_waits_for_a_refiling_and_judges_its_committed_category(vocabulary: SimpleNamespace) -> None:
    """The attach parks on the refiled tag's lock, then reads its new category and refuses."""

    with system_context(reason="tags concurrency test cap"):
        TagCategory.objects.filter(pk=vocabulary.relationship.pk).update(max_per_record=1)
    with actor_context(vocabulary.owner):
        TagAssignment.objects.attach(*vocabulary.address, [vocabulary.prospect.sqid])

    def attach_loose() -> None:
        with actor_context(vocabulary.owner):
            TagAssignment.objects.attach(*vocabulary.address, [vocabulary.loose.sqid])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="tags concurrency test refile"):
            loose = Tag.objects.get(pk=vocabulary.loose.pk)
            loose.category_id = vocabulary.relationship.pk
            loose.save()
            contender, pid = submit(pool, attach_loose)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError, match="at most 1 tag from Relationship"):
            contender.result(timeout=10)
    assert _held(vocabulary.party) == {"Prospect"}
