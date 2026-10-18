"""Historical import path kept for migration files generated before the ``angee.base`` → ``angee``
rename.

A stack that keeps its database keeps its generated runtime migrations; those files import the
symbols below from this package. The modules only re-export the owning implementation. New code
imports the owner. The aliases leave once every stack has collapsed its history through
``reset_migration_history``.
"""
