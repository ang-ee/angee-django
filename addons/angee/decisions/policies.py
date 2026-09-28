"""Group settlement strategies selected through the framework registry."""

from typing import Any

from angee.base.impl import ImplBase


class DecisionPolicy(ImplBase):
    """Registered strategy deciding when a group's answers are sufficient."""

    @classmethod
    def settled(cls, decisions: list[Any]) -> bool:
        """Return whether the supplied ordered seats satisfy this policy."""
        raise NotImplementedError


class First(DecisionPolicy):
    """Settle on the first final seat."""

    key = "first"
    label = "First"

    @classmethod
    def settled(cls, decisions: list[Any]) -> bool:
        """Accept any final seat as sufficient."""
        return any(not decision.is_open for decision in decisions)


class All(DecisionPolicy):
    """Settle when every seat is final."""

    key = "all"
    label = "All"

    @classmethod
    def settled(cls, decisions: list[Any]) -> bool:
        """Require a nonempty group with no open seats."""
        return bool(decisions) and all(not decision.is_open for decision in decisions)
