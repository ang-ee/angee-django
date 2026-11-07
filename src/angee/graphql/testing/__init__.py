"""GraphQL test support: execute documents as an actor and pin their SQL cost.

Import from ``angee.graphql.testing.documents``. The helpers are plain functions,
so a bare source-model suite under pytest, a composed host under Django's test
runner and a consumer suite share them. The module imports before
``django.setup()``. This package is not a Django app and does not define models.
Serving code must not import test support.
"""
