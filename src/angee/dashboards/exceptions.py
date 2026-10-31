"""Dashboard persistence conflicts."""

class DashboardConflictError(Exception):
    """The caller's persisted dashboard identity is no longer current."""

    def __init__(self, current_revision: int | None = None) -> None:
        self.current_revision = current_revision
        super().__init__("The dashboard changed since it was loaded.")
