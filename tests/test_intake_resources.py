"""A Need resource row answers its access question once, through the request owner."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.intake.choices import NeedAccessAction
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from tests.conftest import make_addon

User = get_user_model()
USERS = f"010_{User._meta.label_lower}.yaml"


def _user(xref: str) -> str:
    return f"- _xref: {xref}\n  username: request-{xref}\n  email: {xref}@example.com\n  password: \"!\"\n"


def _addon(tmp_path: Path, needs: str, *, task_owner: str = "decider"):
    files = {
        USERS: _user("decider") + _user("requester") + _user("refused") + _user("outsider"),
        "020_parties.person.yaml": (
            "- _xref: requester_person\n  display_name: Requester\n  user: access_seed.requester\n"
            "- _xref: denied_person\n  display_name: Denied requester\n  user: access_seed.refused\n"
        ),
        "030_projects.task.yaml": (
            f"- _xref: target\n  title: Requested work\n  owner: access_seed.{task_owner}\n"
        ),
        "040_intake.need.yaml": needs,
    }
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return make_addon(name="tests.access_seed", label="access_seed", path=tmp_path, resources={"install": list(files)})


def _load(addon):
    return Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])


NEEDS = (
    "- _xref: approved\n  task: access_seed.target\n  party: access_seed.requester_person\n  body: Please let me in.\n"
    "  access: approve\n  access_by: access_seed.decider\n"
    "- _xref: denied\n  task: access_seed.target\n  party: access_seed.denied_person\n  body: Me too.\n"
    "  access: deny\n  access_by: access_seed.decider\n"
    "- _xref: pending\n  task: access_seed.target\n  body: Still waiting.\n  claimed_email: waiting@example.com\n"
)


@pytest.mark.django_db(transaction=True)
def test_rows_answer_open_access_questions_as_the_named_decider(composed_tables: None, tmp_path: Path) -> None:
    del composed_tables
    addon = _addon(tmp_path, NEEDS)

    _load(addon)

    need = apps.get_model("intake", "Need")
    with system_context(reason="test.access_resources.readback"):
        decider = User.objects.get(username="request-decider")
        approved = need.objects.get(body="Please let me in.")
        denied = need.objects.get(body="Me too.")
        pending = need.objects.get(body="Still waiting.")
        assert approved.access_verdict == [NeedAccessAction.INTAKE_APPROVE]
        assert approved.access_answered_by_id == decider.pk
        assert approved.requester_access_granted
        assert approved.admitted_user.username == "request-requester"
        assert denied.access_verdict == [NeedAccessAction.INTAKE_DENY]
        assert not denied.requester_access_granted
        assert pending.access_decision_id is not None and pending.access_verdict is None

    replay = _load(addon)

    assert replay.created == 0 and replay.updated == 0
    with system_context(reason="test.access_resources.replay"):
        assert need.objects.get(pk=approved.pk).access_decision_id == approved.access_decision_id


@pytest.mark.django_db(transaction=True)
def test_an_answered_question_keeps_its_answer_when_the_row_changes(composed_tables: None, tmp_path: Path) -> None:
    del composed_tables
    addon = _addon(tmp_path, NEEDS)
    _load(addon)
    (tmp_path / "040_intake.need.yaml").write_text(NEEDS.replace("access: deny", "access: approve"))

    _load(addon)

    with system_context(reason="test.access_resources.kept"):
        denied = apps.get_model("intake", "Need").objects.get(body="Me too.")
        assert denied.access_verdict == [NeedAccessAction.INTAKE_DENY]


@pytest.mark.django_db(transaction=True)
def test_the_request_owner_refuses_a_decider_without_share(composed_tables: None, tmp_path: Path) -> None:
    del composed_tables
    addon = _addon(tmp_path, NEEDS, task_owner="outsider")

    with pytest.raises(ResourceLoadError, match="requires need write and target share"):
        _load(addon)

    with system_context(reason="test.access_resources.refused"):
        assert not apps.get_model("intake", "Need").objects.filter(body="Please let me in.").exists()
