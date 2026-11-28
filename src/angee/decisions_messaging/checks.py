"""Decision mail's system checks, registered by ``DecisionsMessagingConfig.ready()``."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core import checks

from angee.decisions_messaging.models import MailAudience

SETTING = "ANGEE_DECISIONS_MESSAGING_AUDIENCES"


def check_audiences(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Refuse an ``ANGEE_DECISIONS_MESSAGING_AUDIENCES`` that is not a mapping of decision kinds to audiences.

    Each key is a ``Decision.kind`` as asked, or ``"*"`` for every kind not named, and
    each value one of ``MailAudience``, so a misspelt audience fails at start-up
    instead of in a mail task. The error names the first entry refused.
    """

    del app_configs, kwargs
    audiences = settings.ANGEE_DECISIONS_MESSAGING_AUDIENCES
    if not isinstance(audiences, dict):
        problem = f"is a {type(audiences).__name__}, not a mapping of decision kinds to audiences"
    else:
        refused = next(
            ((kind, audience) for kind, audience in audiences.items()
             if not (isinstance(kind, str) and kind) or audience not in MailAudience.values),
            None,
        )
        if refused is None:
            return []
        problem = f"maps {refused[0]!r} to {refused[1]!r}; a key is a decision kind or \"*\""
    return [
        checks.Error(
            f"{SETTING} {problem}, and each audience one of {', '.join(MailAudience.values)}.",
            obj=SETTING,
            id="decisions_messaging.E001",
        )
    ]
