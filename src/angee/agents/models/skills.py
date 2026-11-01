"""Agent skills owners and their native model contracts."""

from __future__ import annotations

import json
from typing import Any

import yaml
from django.db import models, transaction
from django.utils import timezone
from rebac import database_context, system_context

from angee.base.models.audit import AuditMixin
from angee.iam.models.base import ProtectedDataModel
from angee.integrate_vcs.registry import VcsOutput, VcsOutputManager
from angee.provenance.contrib.angee.managers import AngeeHistoryManager


class SkillManager(VcsOutputManager, AngeeHistoryManager):
    """Manager owning the reconcile of skill rows from a skill source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``SKILL.md`` and upsert/prune :class:`Skill` rows."""

        repository: Any = source.repository
        vcs_bridge = repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="SKILL.md", parse=self.model.parse_manifest)
        seen: set[Any] = set()
        using = self._db or source._state.db or self.db
        rows = self.db_manager(using)
        with database_context(using), system_context(reason="agents.skill.sync"), transaction.atomic(using=using):
            for descriptor in descriptors:
                skill, _created = rows.update_or_create(
                    source_id=source.pk,
                    path=str(descriptor.get("path", "")),
                    defaults={
                        "name": str(descriptor.get("name", "")),
                        "description": str(descriptor.get("description", "")),
                        "metadata": dict(descriptor.get("metadata", {})),
                    },
                )
                seen.add(skill.pk)
            rows.filter(source=source).exclude(pk__in=seen).delete()
            source.complete_sync(now=timezone.now())
        return len(descriptors)


class Skill(VcsOutput, AuditMixin, ProtectedDataModel, concrete=True):
    """One skill discovered under an ``integrate_vcs.Source`` (``source_kind="skill"``).

    The operator mounts the skill's directory into an agent's workspace; Django keeps
    the inventory only. Discovery reuses the integrate source walk.
    """

    source_kind = "skill"
    """Binds the ``skill`` source kind to this output model (see ``integrate_vcs.Source``)."""

    sqid_prefix = "skl_"
    source = models.ForeignKey("integrate_vcs.Source", on_delete=models.CASCADE, related_name="skills")
    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    path = models.CharField(max_length=1024, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = SkillManager()

    @staticmethod
    def parse_manifest(blob: bytes) -> dict[str, Any]:
        """Parse one skill manifest's YAML frontmatter into JSON-safe descriptor values."""

        text = blob.decode("utf-8", errors="replace")
        front_matter = ""
        if text.startswith("---"):
            lines = text.splitlines()
            closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
            if closing is not None:
                front_matter = "\n".join(lines[1:closing])
        values = yaml.safe_load(front_matter) if front_matter else {}
        if not isinstance(values, dict):
            values = {}
        metadata = {str(key): value for key, value in values.items() if key not in {"name", "description"}}
        return {
            "name": str(values.get("name", "")), "description": str(values.get("description", "")),
            "metadata": json.loads(json.dumps(metadata, default=str)),
        }

    class Meta:
        """Django model options for discovered skills."""

        abstract = True
        ordering = ("name", "path")
        rebac_resource_type = "agents/skill"
        constraints = (models.UniqueConstraint(fields=("source", "path"), name="uniq_agents_skill_path"),)

    def __str__(self) -> str:
        """Return the skill's display label."""

        return self.name or self.path or f"skill:{self.public_id}"
