"""Plugin-bound retrieval composes the canonical Knowledge page budget."""

import pytest

from angee.knowledge.retrieval import MAX_SEARCH_PAGE_SIZE, RetrievalBackend


@pytest.mark.parametrize("first,expected", [(-1, 0), (0, 0), (5, 5), (1000000, MAX_SEARCH_PAGE_SIZE)])
def test_bound_backend_search_limits_strategy_work_and_result(first: int, expected: int) -> None:
    budgets = []

    class Backend(RetrievalBackend):
        @classmethod
        def search_many(cls, vaults, query, *, first=20):
            budgets.append(first)
            return range(MAX_SEARCH_PAGE_SIZE + 1)

    assert len(list(Backend(None).search("query", first=first))) == expected
    assert budgets == ([expected] if expected else [])
