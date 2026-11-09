"""One concrete test row per passage resource, registered by the root conftest."""

from angee.knowledge_graph_pgvector.models import Passage as AbstractPassage
from angee.knowledge_graph_pgvector.models import PassageEmbedding as AbstractPassageEmbedding


class Passage(AbstractPassage):
    class Meta(AbstractPassage.Meta):
        abstract = False
        app_label = "knowledge_graph_pgvector"
        db_table = "test_pgv_passage"
        rebac_resource_type = "knowledge_graph_pgvector/passage"


class PassageEmbedding(AbstractPassageEmbedding):
    class Meta(AbstractPassageEmbedding.Meta):
        abstract = False
        app_label = "knowledge_graph_pgvector"
        db_table = "test_pgv_passage_embedding"
        rebac_resource_type = "knowledge_graph_pgvector/passage_embedding"
