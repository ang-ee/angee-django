"""Project setup input contributed only by the enabled Knowledge intersection."""

import strawberry

from angee.graphql.ids import PublicID
from angee.graphql.inputs import InputReference


@strawberry.input(name="ProjectSetupInput", extend=True)
class ProjectKnowledgeSetupInput:
    """Knowledge's declared project setup act."""

    vault_template: PublicID = strawberry.field(metadata={InputReference: InputReference("knowledge.Vault")})


schemas = {
    "public": {"input_extensions": [ProjectKnowledgeSetupInput]},
    "console": {"input_extensions": [ProjectKnowledgeSetupInput]},
}
