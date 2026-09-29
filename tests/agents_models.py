"""Shared concrete inference catalogue targets for source-addon tests."""

from angee.agents.models import InferenceModel as AbstractInferenceModel
from angee.agents.models import InferenceProvider as AbstractInferenceProvider
from tests.integrate_models import Integration


class InferenceProvider(AbstractInferenceProvider, Integration):
    """Concrete inference capability over the shared integration."""

    class Meta(AbstractInferenceProvider.Meta):
        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_provider"
        rebac_resource_type = "agents/inference_provider"


class InferenceModel(AbstractInferenceModel):
    """Concrete inference catalogue row shared by evidence and agent tests."""

    class Meta(AbstractInferenceModel.Meta):
        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_model"
        rebac_resource_type = "agents/inference_model"
