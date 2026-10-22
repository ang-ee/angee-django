"""Historical import path kept for migration files generated before the ``angee.base`` → ``angee``
rename.

A stack that keeps its database keeps its generated runtime migrations; those files import the
symbols below from this package. Live fields and classes are re-exported from their owners;
``historical_relationships`` retains the frozen operations required by older data migrations.
New code imports the live owner. Compatibility leaves once every stack has collapsed its history through
``reset_migration_history``.
"""
