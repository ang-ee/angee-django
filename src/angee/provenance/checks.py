"""Django checks for provenance attribution boundaries."""

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.conf import settings
from django.core import checks
from rebac import app_settings
from simple_history.models import registered_models

from angee.base.models.merge import MergeableMixin
from angee.provenance.context import changeset_model_label
from angee.provenance.querysets import model_history


def check_changeset_middleware(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require one HTTP attribution boundary and REBAC actor resolution."""

    del app_configs, kwargs
    if not app_settings.REBAC_STRICT_MODE or not changeset_model_label():
        return []
    if not registered_models:
        return []
    middleware = settings.MIDDLEWARE
    actor = "rebac.middleware.ActorMiddleware"
    boundary = "angee.provenance.middleware.ChangesetMiddleware"
    if middleware.count(boundary) == 1 and actor in middleware:
        return []
    return [
        checks.Error(
            "Strict changeset attribution requires one ChangesetMiddleware and REBAC's ActorMiddleware.",
            hint=f"Add one '{boundary}' and '{actor}' in MIDDLEWARE.",
            id="angee.E038",
        )
    ]


def check_merge_history(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require history, including the moved column, on every model a revertible merge moves.

    A merge of a model that keeps history is undone by ``Changeset.revert``,
    which replays history only; a row it moved without history, or through a
    column its history excludes, would stay on the survivor. The moved
    references are the fold's own (:meth:`MergeableMixin.moved_references`), so
    the check and the merge cannot drift. Writes a fold makes outside that
    derivation are not checked here: a pointing model's ``merge_contract``, the
    model's ``retire_merged`` and hooks it runs (a handle's
    ``_party_links_resolved``) answer for their own history.
    """

    del kwargs
    configs = apps.get_app_configs() if app_configs is None else app_configs
    untracked: dict[tuple[str, str], tuple[type, str, str]] = {}
    for config in sorted(configs, key=lambda config: config.label):
        for model in sorted(config.get_models(), key=lambda model: model._meta.label):
            if not issubclass(model, MergeableMixin) or model_history(model) is None:
                continue
            for reference in model.moved_references():
                history = model_history(reference.model)
                excluded = set(reference.columns) & set(history.excluded_fields) if history is not None else set()
                if history is None or excluded:
                    gap = f"excludes {', '.join(sorted(excluded))} from history" if excluded else "keeps no history"
                    key = (reference.model._meta.label, reference.name)
                    untracked.setdefault(key, (reference.model, model._meta.label, gap))
    return [
        checks.Error(
            f"{label}.{name} moves in {merger} merges but {gap}, so reverting the merge cannot restore it.",
            hint="Compose HistoryMixin on the moved model and track the key, or declare it KEEP in merge_policies.",
            obj=pointing,
            id="angee.E051",
        )
        for (label, name), (pointing, merger, gap) in sorted(untracked.items())
    ]
