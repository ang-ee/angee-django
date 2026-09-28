"""Keep the workflow implementation within its declared physical-line budgets."""

import pytest

from tests.tools import measure_workflows
from tests.tools.measure_workflows import main


def test_workflow_backend_budgets(capsys: pytest.CaptureFixture[str]) -> None:
    """The ordinary test suite enforces the measurement command's fixed limits."""

    result = main()
    assert result == 0, capsys.readouterr().out


@pytest.mark.parametrize("name", ["forgotten.py", "testing/forgotten.py", "unlisted.toml", "unlisted.md"])
def test_workflow_budget_rejects_unmapped_files(tmp_path, monkeypatch, name):
    """New source and declaration files must receive an explicit budget decision."""

    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("", encoding="utf-8")
    monkeypatch.setattr(measure_workflows, "ROOT", tmp_path)
    with pytest.raises(ValueError, match=f"Unmapped workflow files: {name}"):
        main()
