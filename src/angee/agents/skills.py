"""Historical skill-manifest parser import; the Skill model owns its values."""

from angee.agents.models import Skill

parse_skill_meta = Skill.parse_manifest
