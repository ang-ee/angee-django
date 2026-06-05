#!/usr/bin/env python3
"""Deterministic red-flag scanner — seeds the reviewer audit with concrete hotspots.

Not a judge: it flags *candidates* against the repo guidelines' mechanical smells
so a reviewer adjudicates them (some flags are legitimate — e.g. a frozen cache
dataclass, a phase-1 AppConfig deferral). Run per unit:

    python .agents/audit/scan.py src/angee/iam
    python .agents/audit/scan.py packages/sdk/src

Each finding prints `CATEGORY  file:line  detail` so it pastes straight into a
reviewer prompt. Backend (.py) and frontend (.ts/.tsx) checks auto-select by file
type. Excludes tests/, runtime/, migrations/, node_modules/, dist/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

EXCLUDE_PARTS = {"tests", "runtime", "migrations", "node_modules", "dist", "__pycache__", ".test-results"}


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Return reviewable source files under ``root`` for the given suffixes."""

    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in suffixes or not path.is_file():
            continue
        if EXCLUDE_PARTS & set(path.parts):
            continue
        if path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
            continue
        out.append(path)
    return out


def _emit(category: str, path: Path, lineno: int, detail: str) -> None:
    """Print one candidate finding in a paste-ready shape."""

    print(f"{category:<26} {path}:{lineno}  {detail}")


def scan_python(path: Path) -> None:
    """Flag Python decomposition / naming / level smells against the backend guidelines."""

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    defines_model_layer = bool(re.search(r"class\s+\w*(Model|Manager|QuerySet)\b", text)) or "AngeeModel" in text
    in_type_checking = False

    # Module filename should be single-word + role-named (Naming rule).
    stem = path.stem
    if "_" in stem and stem not in {"__init__", "asgi", "wsgi"}:
        _emit("naming-multiword-module", path, 1, f"module '{path.name}' is multi-word; prefer single-word + a dir")

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("if TYPE_CHECKING"):
            in_type_checking = True
        elif line and not line[0].isspace() and not stripped.startswith(("#", '"', "'")):
            in_type_checking = False

        # Loose module-level function in a module that also owns model-layer classes.
        if defines_model_layer and re.match(r"def \w", line):
            _emit("scattered-function", path, i, f"top-level def in a model/manager module: {stripped[:70]}")

        # Non-abstract source model — almost always wrong in src/.
        if re.match(r"\s*abstract = False\b", line):
            _emit("non-abstract-source-model", path, i, "abstract=False authored in src/ (composer emits concrete)")

        # Dataclass near the model layer — candidate "should be a model/queryset/value-on-a-class".
        if stripped.startswith("@dataclass") and defines_model_layer:
            _emit(
                "wrong-primitive-dataclass",
                path,
                i,
                "@dataclass in a model-layer module — could be a model/queryset?",
            )

        # Type-switch / name-list heuristics (want polymorphism or a model-owned declaration).
        if re.search(r"\b__name__\s+in\s+[\{(\[]", line):
            _emit("name-list-heuristic", path, i, f"name-list membership switch: {stripped[:70]}")
        if re.search(r"\bisinstance\(", line) and "/tests/" not in str(path):
            _emit("type-switch-isinstance", path, i, f"isinstance switch (adjudicate vs polymorphism): {stripped[:60]}")

        # Function-local / deferred import outside TYPE_CHECKING (imports-at-top rule).
        if re.match(r"\s+(from \w|import \w)", line) and not in_type_checking and "def " not in line:
            _emit("deferred-import", path, i, f"indented import (phase-1/TYPE_CHECKING only): {stripped[:60]}")

        # camelCase function/method name (Python is snake_case).
        m = re.match(r"\s*def ([a-z]+[A-Z]\w*)", line)
        if m:
            _emit("naming-camelcase-def", path, i, f"camelCase def '{m.group(1)}' — Python is snake_case")


def scan_typescript(path: Path) -> None:
    """Flag TypeScript type-safety / naming smells against the frontend guidelines."""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, start=1):
        if re.search(r":\s*any\b|<any>|as any\b", line):
            _emit("ts-any", path, i, f"`any` (frontend guidelines: never any): {line.strip()[:60]}")
        if "as unknown as" in line:
            _emit("ts-double-assert", path, i, f"as-unknown-as cast: {line.strip()[:60]}")
        if re.search(r"Record<\s*string\s*,\s*unknown\s*>", line):
            _emit("ts-record-unknown", path, i, "Record<string, unknown> — usually a missing explicit type")


def main(argv: list[str]) -> int:
    """Scan one unit path and print candidate findings grouped by file type."""

    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    if not root.exists():
        print(f"no such path: {root}")
        return 2
    for py in _iter_files(root, (".py",)):
        scan_python(py)
    for ts in _iter_files(root, (".ts", ".tsx")):
        scan_typescript(ts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
