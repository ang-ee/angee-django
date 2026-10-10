"""Referenced installation identities validate before import and roles after grants."""

import pytest
import yaml
from django.apps import apps
from rebac import system_context
from rebac.memberships import revoke as revoke_membership

from angee.iam.deployment import bind_deployment_operator
from angee.iam.roles import platform_admin_role
from angee.resources.exceptions import ResourceLoadError
from angee.resources.signals import post_load, pre_load
from angee.resources.testing.models import Resource
from angee.spaces.testing.models import Group
from tests.conftest import create_platform_admin, make_addon

pytestmark = pytest.mark.usefixtures("composed_tables")


def group_seed(tmp_path, *, with_grants=False):
    (tmp_path / "group.yaml").write_text(yaml.safe_dump({
        "_meta": {"model": Group._meta.label},
        "rows": [{"xref": "group", "fields": {
            "name": "Imported", "slug": "imported", "owner": "iam.deployment_operator",
        }}],
    }))
    entries = [{"path": "group.yaml"}]
    if with_grants:
        (tmp_path / "grants.yaml").write_text(yaml.safe_dump([{
            "resource": str(platform_admin_role()), "relation": "member", "subject": "iam.deployment_operator",
        }]))
        entries.append({"path": "grants.yaml", "kind": "grants"})
    addon = make_addon(name="tests.operator_seed", path=tmp_path, resources={"install": entries})
    return apps.get_app_config("iam"), addon


def test_pre_load_requires_an_explicit_operator_and_never_adopts_a_single_admin(tmp_path):
    create_platform_admin("unbound-operator")
    selected = group_seed(tmp_path)
    with pytest.raises(ResourceLoadError, match="unbound"):
        Resource.objects.load_addons(selected, tiers=["install"])
    assert not Resource.objects.filter(xref="deployment_operator").exists()


def test_each_load_validates_once_and_dry_run_preserves_explicit_binding(tmp_path):
    operator = create_platform_admin("seed-operator")
    bind_deployment_operator(operator)
    selected = group_seed(tmp_path)
    observed = []

    def before(sender, *, referenced_handles, **kwargs):
        observed.append(("before", referenced_handles))

    def after(sender, *, referenced_handles, **kwargs):
        observed.append(("after", referenced_handles))

    pre_load.connect(before, weak=False)
    post_load.connect(after, weak=False)
    try:
        Resource.objects.load_addons(selected, tiers=["install"], dry_run=True)
        with system_context(reason="test resource dry run"):
            assert not Group.objects.filter(slug="imported").exists()
        Resource.objects.load_addons(selected, tiers=["install"])
    finally:
        pre_load.disconnect(before)
        post_load.disconnect(after)
    handles = frozenset({(apps.get_app_config("iam").name, "deployment_operator")})
    assert observed == [("before", handles), ("after", handles)] * 2
    with system_context(reason="test imported reference"):
        assert Group.objects.get(slug="imported").owner_id == operator.pk
    assert Resource.objects.filter(xref="deployment_operator").count() == 1


def test_operator_role_is_checked_after_the_resource_grants_restore_it(tmp_path):
    operator = create_platform_admin("restored-operator")
    bind_deployment_operator(operator)
    with system_context(reason="test lost operator membership"):
        revoke_membership(subject=operator, container=platform_admin_role())
    selected = group_seed(tmp_path, with_grants=True)
    Resource.objects.load_addons(selected, tiers=["install"])
    with system_context(reason="test restored operator ownership"):
        assert Group.objects.get(slug="imported").owner_id == operator.pk
