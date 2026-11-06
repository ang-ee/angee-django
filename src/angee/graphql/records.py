"""Schema buckets for the record verbs every declaring model shares.

Direct sharing (:mod:`angee.graphql.sharing`) serves records declaring
``rebac_grantable``; trash (:mod:`angee.graphql.trash`) serves records composing
``TrashMixin``; merge (:mod:`angee.graphql.merge`) serves records composing
``MergeableMixin``; related-edge counts (:mod:`angee.graphql.edges`) serve
every record a related group lists rows on.
"""

from angee.graphql.actions import ActionQuery
from angee.graphql.edges import RecordEdgeCountType, RecordEdgeQuery
from angee.graphql.merge import MergeMutation
from angee.graphql.sharing import RecordAccessMutation, RecordAccessOption, RecordAccessQuery, RecordAccessType
from angee.graphql.trash import TrashMutation

schemas = {
    "public": {
        "mutation": [MergeMutation, TrashMutation],
    },
    "console": {
        "query": [RecordAccessQuery, ActionQuery, RecordEdgeQuery],
        "mutation": [MergeMutation, RecordAccessMutation, TrashMutation],
        "types": [RecordAccessType, RecordAccessOption, RecordEdgeCountType],
    },
}
