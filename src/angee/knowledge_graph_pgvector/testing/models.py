"""One concrete test row per passage resource, registered by the root conftest."""

from angee.knowledge_graph_pgvector.models import Entity as AbstractEntity
from angee.knowledge_graph_pgvector.models import EntityEdge as AbstractEntityEdge
from angee.knowledge_graph_pgvector.models import EntityMention as AbstractEntityMention
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


class Entity(AbstractEntity):
    class Meta(AbstractEntity.Meta):
        abstract = False
        app_label = "knowledge_graph_pgvector"
        db_table = "test_pgv_entity"
        rebac_resource_type = "knowledge_graph_pgvector/entity"


class EntityMention(AbstractEntityMention):
    class Meta(AbstractEntityMention.Meta):
        abstract = False
        app_label = "knowledge_graph_pgvector"
        db_table = "test_pgv_entity_mention"
        rebac_resource_type = "knowledge_graph_pgvector/entity_mention"


class EntityEdge(AbstractEntityEdge):
    class Meta(AbstractEntityEdge.Meta):
        abstract = False
        app_label = "knowledge_graph_pgvector"
        db_table = "test_pgv_entity_edge"
        rebac_resource_type = "knowledge_graph_pgvector/entity_edge"
