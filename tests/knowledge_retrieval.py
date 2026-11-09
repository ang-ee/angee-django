"""A declared second retrieval strategy for composed search contracts."""

from django.apps import apps

from angee.knowledge.retrieval import RetrievalBackend, RetrievalHit


class ReverseRetrieval(RetrievalBackend):
    key = "reverse"

    @classmethod
    def search_many(cls, vaults, query, *, first=20):
        pages = (
            apps.get_model("knowledge", "Page")
            .objects.filter(
                vault__in=vaults,
                title__icontains=query,
            )
            .untrashed()
            .scoped()
            .order_by("-title")[:first]
        )
        return [RetrievalHit(page=page, rank=rank) for rank, page in enumerate(pages, 1)]
