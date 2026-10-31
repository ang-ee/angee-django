"""Protected declarations retain model-bound native candidate policies."""

import os
import subprocess
import sys


def test_protected_aspects_preserve_subject_callback_types(tmp_path):
    source = '''
from collections.abc import Sequence
from typing import Any, assert_type
from django.db import models
from angee.provenance.statements import Aspect, StatementRecords

class Subject(models.Model):
    def choose(self, rows: Sequence[Any]) -> Sequence[Any]:
        return rows

class Unrelated(models.Model):
    def choose(self, rows: Sequence[Any]) -> Sequence[Any]:
        return rows

aspect = Aspect(candidates=Subject.choose)
assert_type(aspect, Aspect[Subject])
records = StatementRecords(aspects={"selected": aspect, "ordinary": Aspect(fields=("id",))})
assert_type(records, StatementRecords[Subject])
aspect.resolve(Subject(), [])
'''
    fixture = tmp_path / 'protected_subject_callbacks.py'
    fixture.write_text(source)
    command = [sys.executable, '-m', 'mypy', str(fixture)]
    environment = {**os.environ, 'PYTHONPATH': 'src'}
    accepted = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=180)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    fixture.write_text(source + '''
Aspect[Subject](candidates=Unrelated.choose)
aspect.resolve(Unrelated(), [])
StatementRecords[Subject](aspects={"wrong": Aspect[Unrelated](candidates=Unrelated.choose)})
''')
    refused = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=180)
    assert refused.returncode != 0
    assert refused.stdout.count('error:') == 3, refused.stdout + refused.stderr
    assert '"candidates"' in refused.stdout
    assert '"resolve"' in refused.stdout
    assert 'Dict entry' in refused.stdout
