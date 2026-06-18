"""Django config for the OpenAI inference integration addon."""

from __future__ import annotations

from django.apps import AppConfig


class AgentsIntegrateOpenAIConfig(AppConfig):
    """Source app manifest for OpenAI's inference backend."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.agents_integrate_openai"
    label = "agents_integrate_openai"
    depends_on = ("angee.agents", "angee.integrate")
    resources = {
        "demo": (
            {
                "path": "resources/demo/010_integrate.credential.yaml",
                "depends_on": "iam:resources/demo/010_iam.user.yaml",
                "adopt": ("user", "name"),
            },
            {
                "path": "resources/demo/020_integrate.integration.yaml",
                "depends_on": (
                    "resources/demo/010_integrate.credential.yaml",
                    "integrate:resources/master/010_integrate.vendor.yaml",
                ),
                "adopt": ("owner", "vendor", "impl_class"),
            },
            {
                "path": "resources/demo/030_agents.inferenceprovider.yaml",
                "depends_on": "resources/demo/020_integrate.integration.yaml",
                "adopt": "integration",
            },
            {
                "path": "resources/demo/040_agents.inferencemodel.yaml",
                "depends_on": "resources/demo/030_agents.inferenceprovider.yaml",
                "adopt": ("provider", "name"),
            },
        ),
    }
