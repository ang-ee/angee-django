"""Public errors for native Knowledge content operations."""

class StaleBodyError(ValueError):
    """Raised when a body write expects a hash the stored body no longer has."""


class UnsupportedPageKindError(ValueError):
    """Raised when a body write targets a page without a markdown child."""


class StructuredEditError(ValueError):
    """Base for a structure-aware markdown edit that cannot be applied."""


class SectionNotFoundError(StructuredEditError):
    """Raised when a heading path (or replace target) matches nothing."""


class AmbiguousMatchError(StructuredEditError):
    """Raised when a heading path (or replace target) matches more than once."""
