"""Schema buckets for the record verbs every declaring model shares.

Direct sharing (:mod:`angee.graphql.sharing`) serves records declaring
``rebac_grantable``; trash (:mod:`angee.graphql.trash`) serves records composing
``TrashMixin``; merge (:mod:`angee.graphql.merge`) serves records composing
``MergeableMixin``; related counts (:mod:`angee.graphql.related_counts`) serve
every record a related group lists rows on.
"""

from angee.graphql.actions import ActionQuery
from angee.graphql.merge import MergeMutation
from angee.graphql.related_counts import RelatedCountQuery, RelatedCountType
from angee.graphql.sharing import RecordAccessMutation, RecordAccessOption, RecordAccessQuery, RecordAccessType
from angee.graphql.trash import TrashMutation

schemas = {
    "public": {
        "mutation": [MergeMutation, TrashMutation],
    },
    "console": {
        "query": [RecordAccessQuery, ActionQuery, RelatedCountQuery],
        "mutation": [MergeMutation, RecordAccessMutation, TrashMutation],
        "types": [RecordAccessType, RecordAccessOption, RelatedCountType],
    },
}
