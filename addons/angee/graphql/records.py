"""Schema buckets for the record verbs every declaring model shares.

Direct sharing (:mod:`angee.graphql.sharing`) serves records declaring
``rebac_grantable``; trash (:mod:`angee.graphql.trash`) serves records composing
``TrashMixin``.
"""

from angee.graphql.sharing import RecordAccessMutation, RecordAccessOption, RecordAccessQuery, RecordAccessType
from angee.graphql.trash import TrashMutation

schemas = {
    "public": {
        "mutation": [TrashMutation],
    },
    "console": {
        "query": [RecordAccessQuery],
        "mutation": [RecordAccessMutation, TrashMutation],
        "types": [RecordAccessType, RecordAccessOption],
    },
}
