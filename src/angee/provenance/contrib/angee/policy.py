"""Protected principal and operation admission at the native provenance boundary."""

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models
from rebac import PermissionDenied, RebacMixin, SubjectRef, to_subject_ref
from rebac.actors import is_sudo
from rebac.resources import model_for_subject_type, model_resource_type

from angee.iam.permissions import require_record_access
from angee.provenance.engine.policy import Policy, get_policy


class AngeePolicy(Policy):
    """Keep SubjectRef identity separate from REBAC authority and source classification."""

    def resolve_source(self, source: object, *, using: str | None = None) -> models.Model:
        if isinstance(source, models.Model):
            alias = using or source._state.db or type(source)._base_manager.db_manager(hints={"instance": source}).db
            return super().resolve_source(source, using=alias)
        reference = SubjectRef.parse(source) if isinstance(source, str) else to_subject_ref(source)
        resolved = model_for_subject_type(reference.subject_type)
        if resolved is None or reference.optional_relation or reference.subject_id == "*":
            raise ValidationError("An asserting source must identify one declared principal.")
        model, id_attr = resolved
        try:
            return model._base_manager.using(using).get(**{id_attr: reference.subject_id})
        except ObjectDoesNotExist as error:
            raise ValidationError("The asserting source must exist on the subject database.") from error

    def source_kind(self, source: models.Model) -> str:
        from angee.provenance.statements import StatementSourceMixin

        if not isinstance(source, StatementSourceMixin):
            raise ValidationError("The source must declare a known per-row source_kind.")
        return super().source_kind(source)

    def authorize(self, actor: object, action: str, instance: models.Model) -> None:
        if action == "export":
            if not is_sudo():
                raise PermissionDenied("Lossless history export requires a system boundary.")
            return
        if not isinstance(instance, RebacMixin) or model_resource_type(instance) is None:
            raise ImproperlyConfigured("Protected provenance operations require RebacMixin.")
        require_record_access(
            instance,
            {"view": "read", "add": "create", "change": "write"}.get(action, action),
            actor=to_subject_ref(actor) if actor is not None else None,
        )

    def history_user(self, actor: object) -> models.Model | None:
        if actor is None:
            return None
        try:
            source = self.resolve_source(actor)
        except TypeError, ValueError, ValidationError:
            return None
        return super().history_user(source)


def angee_policy() -> AngeePolicy:
    """Resolve the native configured policy and require this protected adapter contract."""
    policy = get_policy()
    if not isinstance(policy, AngeePolicy):
        raise ImproperlyConfigured("angee.provenance requires PROVENANCE_POLICY to extend AngeePolicy.")
    return policy
