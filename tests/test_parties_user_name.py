"""An account's person follows the account's name after creation."""

from django.contrib.auth import get_user_model
from rebac import actor_context, system_context

from tests.iam_campaign import Person
from tests.iam_campaign import iam_admin as iam_admin

User = get_user_model()


def _display_name(user):
    return Person._base_manager.get(user=user).display_name


def test_person_display_name_follows_account_renames(iam_admin):
    with actor_context(iam_admin):
        user = User.objects.create_person("ada", "ada@example.com", first_name="Ada", last_name="Byron")
    assert _display_name(user) == "Ada Byron"

    with actor_context(iam_admin):
        User.objects.with_actor(iam_admin).get(pk=user.pk).rename(
            first_name="Ada", last_name="Lovelace", confirmed=True, expected_revision=user.account_revision,
        )
    assert _display_name(user) == "Ada Lovelace"

    with actor_context(iam_admin):
        User.objects.with_actor(iam_admin).get(pk=user.pk).update_account({"first_name": "", "last_name": ""})
    assert _display_name(user) == "ada"


def test_saves_that_cannot_change_the_name_leave_the_person_alone(iam_admin):
    with actor_context(iam_admin):
        user = User.objects.create_person("grace", "grace@example.com", first_name="Grace", last_name="Hopper")
    Person._base_manager.filter(user=user).update(display_name="Curated label")
    stored = User._base_manager.get(pk=user.pk)
    with actor_context(iam_admin):
        User.objects.with_actor(iam_admin).get(pk=user.pk).set_active(
            False, confirmed=True, expected_revision=stored.account_revision,
        )
    with system_context(reason="test.parties.user_name"):
        User.objects.get(pk=user.pk).update_preferences({"theme": "dark"})
    assert _display_name(user) == "Curated label"


def test_accounts_without_a_person_rename_cleanly(iam_admin):
    with system_context(reason="test.parties.user_name"):
        user = User.objects.create_user("no-person")
        user.first_name = "Linked"
        user.save(update_fields=["first_name"])
    assert not Person._base_manager.filter(user=user).exists()
