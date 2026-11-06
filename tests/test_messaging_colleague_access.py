"""Inactive follower accounts stay out of the colleague resource."""

from django.contrib.auth import get_user_model

from tests.test_parties_follower_identity import (  # noqa: F401
    _allowed,
    follower_schema,
    follower_storage,
    followers,
)
from tests.test_project_access import project_access_schema  # noqa: F401


def test_inactive_follower_account_is_not_a_colleague(followers):  # noqa: F811
    f = followers
    users = get_user_model()
    users._base_manager.filter(pk=f.person.user_id).update(is_active=False)
    account = users._base_manager.get(pk=f.person.user_id)
    in_users_resource = users.objects.colleagues(f.reader).filter(pk=account.pk).exists()
    allowed = _allowed(account, f.reader, "colleague")
    assert not allowed and not in_users_resource
