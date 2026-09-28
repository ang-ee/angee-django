"""Keep the workflow implementation within its declared physical-line budgets."""

import pytest

from tests.tools.measure_workflows import main


def test_workflow_backend_budgets(capsys: pytest.CaptureFixture[str]) -> None:
    """The ordinary test suite enforces the measurement command's fixed limits."""

    result = main()
    assert result == 0, capsys.readouterr().out
