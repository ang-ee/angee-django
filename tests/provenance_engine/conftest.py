import pytest


@pytest.fixture
def actor(django_user_model):
    return django_user_model.objects.create_superuser(username="maintainer", password="test")


def pytest_collection_modifyitems(config, items):
    from django.db import connection

    if connection.vendor == "postgresql":
        return
    for item in items:
        if "postgresql" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="PostgreSQL concurrency lane"))
