"""Historical callable import for materialized owner-column migrations.

Integrate, parties and workflows runtime migration sources are fingerprinted;
their original imports remain valid until those histories are retired.
"""

from angee.base.models.audit import retained_set_null as retained_set_null
