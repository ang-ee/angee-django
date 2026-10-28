"""Historical on_delete import retained for existing project migration files.

Remove after every supported project has replaced migrations importing this path.
New migrations serialize the canonical angee.base.models.audit owner.
"""

from angee.base.models.audit import retained_set_null as retained_set_null
