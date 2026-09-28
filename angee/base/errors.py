"""Transport-independent domain refusals."""


class DomainError(Exception):
    """A refusal with a stable wire code and no detail.

    Raise concrete subclasses with a nonempty code, never this base directly.
    """

    code: str

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        code = getattr(cls, "code", None)
        if not isinstance(code, str) or not code.strip():
            raise TypeError("DomainError subclasses require a non-empty string code.")

    def __init__(self) -> None:
        super().__init__(self.code)
