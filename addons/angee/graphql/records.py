"""Schema buckets for the record verbs every declaring model shares.

Direct sharing (:mod:`angee.graphql.sharing`) serves records declaring
``rebac_grantable``; trash (:mod:`angee.graphql.trash`) serves records composing
``TrashMixin``; merge (:mod:`angee.graphql.merge`) serves records composing
``MergeableMixin``.
"""

from angee.graphql.merge import MergeMutation
from angee.graphql.sharing import RecordAccessMutation, RecordAccessOption, RecordAccessQuery, RecordAccessType
from angee.graphql.trash import TrashMutation

schemas = {
    "public": {
        "mutation": [MergeMutation, TrashMutation],
    },
    "console": {
        "query": [RecordAccessQuery],
        "mutation": [MergeMutation, RecordAccessMutation, TrashMutation],
        "types": [RecordAccessType, RecordAccessOption],
    },
}
