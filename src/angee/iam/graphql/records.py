"""Schema buckets for the record verbs every declaring model shares.

Direct sharing (:mod:`angee.iam.graphql.sharing`) serves records declaring
``rebac_grantable``; trash (:mod:`angee.iam.graphql.trash`) serves records composing
``TrashMixin``; merge (:mod:`angee.iam.graphql.merge`) serves records composing
``MergeableMixin``.
"""

from angee.iam.graphql.merge import MergeMutation
from angee.iam.graphql.sharing import RecordAccessMutation, RecordAccessOption, RecordAccessQuery, RecordAccessType
from angee.iam.graphql.trash import TrashMutation

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
