"""A declared second retrieval strategy for composed search contracts."""

from django.apps import apps

from angee.knowledge.retrieval import RetrievalBackend


class ReverseRetrieval(RetrievalBackend):
    key = "reverse"

    @classmethod
    def search_many(cls, vaults, query, *, first=20):
        return apps.get_model("knowledge", "Page").objects.filter(
            vault__in=vaults, title__icontains=query,
        ).untrashed().scoped().order_by("-title")[:first]
