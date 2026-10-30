"""Explicit built-distribution data for metadata-only catalogue tests."""

import csv
import io
import json
import zipfile
from email.message import EmailMessage
from pathlib import Path


def wheel_files(app="catalog_remote.base", distribution="catalog-remote-base", *, requirements=(),
                description="", keywords=(), category="", document=None, code=False):
    message = EmailMessage()
    for key, value in (("Metadata-Version", "2.4"), ("Name", distribution), ("Version", "1.0"),
                       ("Summary", description), ("Keywords", ",".join(keywords))):
        message[key] = value
    for requirement in requirements:
        message["Requires-Dist"] = requirement
    directory = distribution.replace("-", "_") + "-1.0.dist-info"
    files = {f"{directory}/METADATA": message.as_string(),
             f"{directory}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    if app is not None:
        files[f"{directory}/entry_points.txt"] = f"[angee.addons]\n{app} = {app}\n"
        files[f"{app.replace('.', '/')}/pyproject.toml"] = (
            '[tool.angee.platform]\ncategory=' + json.dumps(category) + '\n' if document is None else document
        )
        if code:
            files[f"{app.replace('.', '/')}/__init__.py"] = "raise AssertionError('addon imported')\n"
    record = io.StringIO()
    csv.writer(record).writerows((name, "", "") for name in files)
    files[f"{directory}/RECORD"] = record.getvalue()
    return files


def write_wheel(path: Path, *args, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in wheel_files(*args, **kwargs).items():
            archive.writestr(name, content)
    return path
