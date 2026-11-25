"""Mutate the message-template rules that keep a value text, and see which tests notice.

Each mutation drops or widens one rule in ``src/angee/messaging_templates``, runs the guarding tests twice (the
render module with its hand-written example rows, then the value properties on generated examples alone, Hypothesis
seed 0, no example database), and restores the files. A mutation marked as a real break must fail the properties on
generated examples alone; one that survives there is a gap in the generator, not a row to add.

Run from the framework root, in a slot whose addon files hold no uncommitted change:
``env -u DATABASE_URL uv run --locked python scripts/messaging_templates_mutations.py [label prefix...]``.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "src/angee/messaging_templates"
MODULE = "tests/test_messaging_templates_render.py"
PROPERTIES = "never_becomes_markup"

type Edit = tuple[str, str, str]

_MARKUP_PLACE: Edit = (
    "walker.py", "elif name in MARKUP_FILTERS and (not printed or position != len(names) - 1):", "elif False:")
_SAFE_MARKS: list[Edit] = [
    ("engine.py", "return result if writes_markup or not isinstance(result, SafeData) else str.__str__(result)",
     "return result"),
    ("engine.py", 'charged.__dict__["is_safe"] = writes_markup', "pass"),
]
"""The two rules that each keep a cut line break escaped: either alone still holds."""

MUTATIONS: dict[str, tuple[bool, list[Edit]]] = {
    "M0 no mutation": (False, []),
    "M1 admit a self-closing style": (True, [(
        "html_checks.py", "if closing and tag not in VOID_ELEMENTS and not office",
        'if closing and tag not in VOID_ELEMENTS and tag != "style" and not office')]),
    "M2 drop rule 5": (False, [(
        "html_checks.py", 'if not _ELEMENT_NAME.fullmatch(tag) or written.lower() != f"</{tag}>":',
        "if not _ELEMENT_NAME.fullmatch(tag):")]),
    "M3 drop the position check": (False, [(
        "html_checks.py", "if self.following is not None and start > self.following:", "if False:")]),
    "M4 admit an unquoted value": (True, [(
        "html_checks.py", "quoted = '=\"' + _written(value) + '\"'",
        "quoted = '=\"' + _written(value) + '\"' if written.startswith('=\"', at) else '=' + value")]),
    "M5 drop rule 1": (True, [
        ("html_checks.py", "    if SOURCE_CONTROLS.search(form):", "    if False:"),
        ("walker.py", "if holding := [column for column, text in texts.items() if SOURCE_CONTROLS.search(text)]:",
         "if holding := []:"),
    ]),
    "M6 admit < in title and style": (True, [(
        "html_checks.py", '        if "<" in data:', '        if "<" in data and not self.cdata_elem:')]),
    "M7 drop the element list": (True, [(
        "html_checks.py", "if tag not in MAIL_ELEMENTS and not office:", "if False:")]),
    "M8 drop rule 8": (True, [
        ("html_checks.py", "    if checker.cdata_elem:\n", "    if False:\n"),
        ("html_checks.py", '    elif checker.rawdata and not checker.rawdata.startswith("&"):', "    elif False:"),
    ]),
    "M9 gaps take any white space": (False, [(
        "html_checks.py", '_GAP = " \\t\\n"', '_GAP = " \\t\\n\\r\\x0c\\x0b"')]),
    "M10 drop the markup filter's place": (False, [_MARKUP_PLACE]),
    "M11 keep Django's safe marks": (False, _SAFE_MARKS),
    "M16 drop both: the place and the safe marks": (True, [_MARKUP_PLACE, *_SAFE_MARKS]),
    "M12 drop the attribute list": (False, [("html_checks.py", "if name not in MAIL_ATTRIBUTES:", "if False:")]),
    "M13 any scheme in a link": (True, [
        ("engine.py", "if not colon or scheme not in LINK_SCHEMES:", "if not colon:"),
        ("engine.py", 'if scheme in ("https", "http") and not (rest.startswith("//") and rest[2:3] not in '
         '("", "/", "?", "#")):', "if False:"),
    ]),
    "M14 any character set": (False, [(
        "html_checks.py", "if name == \"charset\" and value.lower() != MAIL_CHARSET:", "if False:")]),
    "M15 keep a value's control characters": (False, [
        ("walker.py", 'return _CONTROLS.sub("", value)', "return value"),
    ]),
}
"""Each mutation: whether what it admits is a real break of promise 1 (a value becomes markup, an attribute, a link
target, CSS or script), and its edits. The others widen what an author writes, or what a value carries where it
stays text, and are held by named tests."""

PROFILE = '''
from hypothesis import Phase, settings

settings.register_profile("examples", database=None, deadline=None)
settings.register_profile("generated", phases=[Phase.generate], database=None, deadline=None)
'''


def run(arguments: list[str], plugins: Path) -> tuple[str, dict[str, int]]:
    """Run pytest on the render module, returning its summary line and how often each test failed."""

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2", "--hypothesis-seed=0",
         "-p", "messaging_templates_profiles", *arguments],
        cwd=ROOT, capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONPATH": str(plugins)},
    )
    failed: dict[str, int] = {}
    for line in done.stdout.splitlines():
        if line.startswith(("FAILED", "ERROR")):
            name = re.sub(r"\[.*", "", line.split("::", 1)[1].split(" ")[0])
            failed[name] = failed.get(name, 0) + 1
    lines = done.stdout.strip().splitlines()
    return (lines[-1] if lines else done.stderr[-300:]), failed


def main() -> None:
    """Apply each mutation in turn, run both modes, print what failed, and restore the addon."""

    only = sys.argv[1:]
    changed = subprocess.run(["git", "status", "--porcelain", "--", str(ADDON)], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
    if changed.strip():
        sys.exit(f"The addon holds uncommitted changes; commit them first:\n{changed}")
    pristine = {path: path.read_text() for path in ADDON.glob("*.py")}
    for label, (_, edits) in MUTATIONS.items():
        for name, old, _new in edits:
            if (count := pristine[ADDON / name].count(old)) != 1:
                sys.exit(f"{label}: {old!r} is in {name} {count} times; update the mutation")
    with tempfile.TemporaryDirectory() as plugins:
        (Path(plugins) / "messaging_templates_profiles.py").write_text(PROFILE)
        try:
            for label, (real, edits) in MUTATIONS.items():
                if only and not any(label.startswith(prefix) for prefix in only):
                    continue
                for path, text in pristine.items():
                    path.write_text(text)
                for name, old, new in edits:
                    path = ADDON / name
                    path.write_text(path.read_text().replace(old, new))
                print(f"{label} (real break: {'yes' if real else 'no'})", flush=True)
                for mode, arguments in (
                    ("with examples", ["--hypothesis-profile=examples", MODULE]),
                    ("generated only", ["--hypothesis-profile=generated", MODULE, "-k", PROPERTIES]),
                ):
                    summary, failed = run(arguments, Path(plugins))
                    print(f"    {mode}: {summary}", flush=True)
                    for test, count in sorted(failed.items()):
                        print(f"        FAILED {test} x{count}", flush=True)
        finally:
            for path, text in pristine.items():
                path.write_text(text)


if __name__ == "__main__":
    main()
