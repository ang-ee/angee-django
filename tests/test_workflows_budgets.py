"""Keep the workflow implementation within its declared physical-line budgets."""

import pytest

from tests.tools import measure_workflows
from tests.tools.measure_workflows import main


def test_workflow_budgets(capsys: pytest.CaptureFixture[str]) -> None:
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


@pytest.mark.parametrize("web_lines, expected", [(2200, 0), (2201, 1)])
def test_workflow_web_budget_is_independent_and_enforced(tmp_path, monkeypatch, capsys, web_lines, expected):
    """Authored web sources fill their budget independently of dependency artifacts."""

    sources = {
        "managers.py": "class DraftSave: pass\nclass WorkflowManager: pass\n",
        "definition.py": "class Definition:\n    def ready_nodes(self): pass\n    def _edge_live(self): pass\n",
        "bindings.py": "",
        "tasks.py": "",
        "web/package.json": "{}\n",
        "web/src/index.tsx": "// source line\n" * (web_lines - 1),
        "web/node_modules/.bin/vitest": "# generated shim\n" * 2201,
        "web/node_modules/.vite/vitest/results.json": "{}\n",
    }
    for name, content in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(measure_workflows, "ROOT", tmp_path)

    assert main() == expected
    report = capsys.readouterr().out
    assert "| Execution | 2 | 1500 | 1498 |" in report
    assert "| Definition, validation, bindings | 3 | 1000 | 997 |" in report
    assert f"| Workflows web | {web_lines} | 2200 | {2200 - web_lines} |" in report
