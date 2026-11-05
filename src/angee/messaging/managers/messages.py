"""Messaging messages factories and write owners."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from contextvars import Context
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from django.utils.html import strip_tags
from rebac import (
    PermissionDenied,
    SubjectRef,
    actor_context,
    current_actor,
    database_context,
    is_sudo,
    system_context,
    to_subject_ref,
)
from rebac.actors import is_anonymous_actor

from angee.base.models.identity import instances_from_public_ids
from angee.base.serialization import STRINGIFIED_JSON, canonical_json_sha256, strip_null_bytes
from angee.base.signals import mute_changes
from angee.graphql.actions import ActionResult, ActionSelectionInput, many_actions, validate_action_selection
from angee.iam.managers import ProtectedManager
from angee.iam.permissions import actor_user_id
from angee.integrate.errors import IntegrationError, safe_integration_failure
from angee.integrate.net import AddressNotAllowedError
from angee.jobs.timeouts import task_claim_lease
from angee.messaging.backends import DeliveryOutcome, DeliveryUncertain, ParsedPart
from angee.messaging.constants import (
    DELIVERY_TASK_EXPIRES,
    MAX_DELIVERY_ATTEMPTS,
    MAX_RECONCILE_ATTEMPTS,
    RECONCILE_BACKOFF,
)
from angee.messaging.events import message_ingested
from angee.messaging.querysets import (
    ChannelQuerySet as ChannelQuerySet,
)
from angee.messaging.querysets import (
    MessageQuerySet as MessageQuerySet,
)
from angee.messaging.querysets import (
    PartQuerySet as PartQuerySet,
)
from angee.messaging.querysets import (
    ThreadFollowerQuerySet as ThreadFollowerQuerySet,
)
from angee.messaging.querysets import (
    ThreadNotificationQuerySet as ThreadNotificationQuerySet,
)
from angee.messaging.querysets import (
    ThreadQuerySet as ThreadQuerySet,
)
from angee.messaging.source_copies import observe_email_identity
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.storage.uploads import fallback_attachment_name

if TYPE_CHECKING:

    from angee.messaging.backends import ParsedMessage
    from angee.messaging.models import (
        Channel,
        Message,
        MessageStar,
        MessageSubtype,
        Reaction,
        Thread,
    )

from angee.messaging.managers.content import derived_part_name
from angee.messaging.querysets import _external_id_annotated

logger = logging.getLogger(__name__)


class DeliveryReconciliationBusy(ValidationError):
    """An operator cannot supersede a live reconciliation worker."""

    def __init__(self) -> None:
        super().__init__("Delivery reconciliation is running; wait for it to finish.", code="delivery-reconciling")


# Parsed metadata is an externally controlled JSON envelope. Bound its canonical
# UTF-8 representation at the same conservative size as fragment vector input;
# reject rather than truncate because the envelope is explicitly lossless.
_MESSAGE_METADATA_MAX_BYTES = 512 * 1024


def _bounded_message_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Return null-safe metadata or reject an envelope above the 512 KiB bound."""

    metadata = strip_null_bytes(value)
    encoded = STRINGIFIED_JSON.encode(metadata).encode("utf-8")
    if len(encoded) > _MESSAGE_METADATA_MAX_BYTES:
        raise ValidationError({"metadata": f"message metadata exceeds {_MESSAGE_METADATA_MAX_BYTES} UTF-8 JSON bytes"})
    return cast(dict[str, Any], metadata)


def _external_id_q(external_id: str) -> models.Q:
    """Return the indexed lookup predicate for one exact ``external_id``.

    Identity indexes carry ``MD5(external_id)`` — a fixed digest instead of the
    unbounded value — so an exact-value filter alone would seq-scan. Filtering on
    the annotated digest *and* the exact value hits the expression index while the
    exact comparison keeps a (theoretical) digest collision harmless. Callers pair
    this with :func:`_external_id_annotated` and their scope column (channel for
    messages, platform for threads).
    """

    digest = hashlib.md5(external_id.encode("utf-8")).hexdigest()
    return models.Q(_eid_digest=digest, external_id=external_id)


def _edit_history_entry(*, edited_by_id: Any, prev_fragment_hashes: list[str]) -> dict[str, Any]:
    """Return one newest-first ``edit_history`` entry — the shape both edit paths share.

    The replaced text itself is not copied: content-addressed fragments are
    immutable, so the prior hashes are enough to recover exactly what was replaced.
    """

    entry: dict[str, Any] = {
        "edited_at": timezone.now().isoformat(),
        "prev_fragment_hashes": prev_fragment_hashes,
    }
    if edited_by_id is not None:
        entry["edited_by_id"] = str(edited_by_id)
    return entry


def _subtype_scope_label(value: str) -> str:
    """Preserve subtype boundary whitespace/NUL cleanup before native field validation."""

    return strip_null_bytes(value or "").strip()


def message_subtype_options(model_label: str = "") -> tuple[dict[str, Any], ...]:
    """Return follower-selectable subtype options for ``model_label``.

    The option list is deterministic even before any message has created subtype
    rows. Existing global/model rows override labels and flags for their key.
    """

    subtype_model = apps.get_model("messaging", "MessageSubtype")
    options = subtype_model.builtin_options()
    labels = ["", _subtype_scope_label(model_label)]
    for subtype in subtype_model._base_manager.filter(model_label__in=labels, hidden=False).order_by(
        "sequence",
        "key",
        "sqid",
    ):
        options[str(subtype.key)] = {
            "key": str(subtype.key),
            "name": str(subtype.name),
            "description": str(subtype.description),
            "internal": bool(subtype.internal),
            "default": bool(subtype.default),
            "sequence": int(subtype.sequence),
        }
    return tuple(sorted(options.values(), key=lambda option: (option["sequence"], option["key"])))


# The `_order_at` annotation mirrors Message.chronological_key so
# the window/cursor filter on the exact `(sent_at, pk)` tuple the feed displays by —
# a backfilled email (older send time, newer pk) cannot skip, duplicate, or misorder
# at a page boundary. These express the keyset comparison against an `(at, pk)` anchor.


def _preview(body: ParsedPart | None, *, limit: int = 280) -> str:
    """Return a short text preview of a parsed body tree: the first plain body text, else the
    first HTML body stripped of its markup. A preview is user text and never carries tags."""

    plain = _first_body_text(body, html=False)
    if plain:
        return plain[:limit]
    html = _first_body_text(body, html=True)
    return " ".join(strip_tags(html).split())[:limit] if html else ""


def _first_body_text(part: ParsedPart | None, *, html: bool) -> str:
    if part is None:
        return ""
    is_html = (part.type or "").casefold() == "text/html"
    if part.text and part.role not in ("quoted", "signature") and is_html == html:
        return part.text
    for child in part.children:
        found = _first_body_text(child, html=html)
        if found:
            return found
    return ""


# The key under which a message stores the digest of what its last sync wrote, so an
# identical re-sync is recognised as a no-op (see ``MessageManager._ingest_one``).
_SYNC_HASH_KEY = "sync_hash"


def _parsed_part_digest(part: ParsedPart | None) -> Any:
    """Return a JSON-safe digest of a parsed body node and its children."""

    if part is None:
        return None
    return {
        "type": part.type,
        "disposition": part.disposition,
        "role": part.role,
        "text": part.text,
        "name": part.name,
        "cid": part.cid,
        # Attachment bytes are hashed, not carried, so a large file does not inflate
        # the digest while a changed byte still flips it.
        "content": hashlib.sha256(part.content).hexdigest() if part.content is not None else None,
        "children": [_parsed_part_digest(child) for child in part.children],
    }


def _parsed_sync_hash(
    parsed: ParsedMessage,
    *,
    channel_id: Any,
    metadata: dict[str, Any],
    received_at: datetime | None = None,
) -> str:
    """Return a stable digest of everything an ingest of ``parsed`` would write.

    Covers the message columns, its Part tree, and its participants for the given
    channel, so an identical re-sync hashes equal and can skip the rewrite. The thread
    is excluded — a re-thread must still reconcile counters even when nothing else
    changed — and is compared separately by the caller. ``received_at`` is the
    effective receipt time ingest writes; it defaults to the parsed one.
    """

    received_at = received_at if received_at is not None else parsed.received_at
    payload = {
        "channel_id": str(channel_id) if channel_id is not None else None,
        "direction": parsed.direction,
        "subject": strip_null_bytes(parsed.subject),
        "headers": [[name, value] for name, value in strip_null_bytes(list(parsed.headers))],
        "sent_at": parsed.sent_at.isoformat() if parsed.sent_at is not None else None,
        "received_at": received_at.isoformat() if received_at is not None else None,
        "metadata": metadata,
        "sender": (
            None
            if parsed.sender is None
            else {
                "platform": parsed.sender.platform,
                "value": parsed.sender.value,
                "display_name": parsed.sender.display_name,
            }
        ),
        "recipients": [
            {
                "platform": recipient.handle.platform,
                "value": recipient.handle.value,
                "display_name": recipient.handle.display_name,
                "role": recipient.role,
            }
            for recipient in parsed.recipients
        ],
        "body": _parsed_part_digest(parsed.body),
    }
    if parsed.in_reply_to:
        # Present only when set: non-reply hashes stay stable across the reply-pointer
        # rollout, so a full re-import rewrites exactly the rows that gain a parent.
        payload["in_reply_to"] = parsed.in_reply_to
    # Existing source hashes include native JSON's spaces; preserve that persisted spelling.
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return canonical_json_sha256(chunks=(canonical,))


class MessageSubtypeManager[SubtypeT: MessageSubtype](ProtectedManager[SubtypeT]):
    """Resolve catalogue identity, retaining the first declared source metadata."""

    def resolve(
        self,
        key: str,
        *,
        model_label: str = "",
        name: str | None = None,
        description: str | None = None,
        internal: bool = False,
        created_by_id: Any = None,
    ) -> SubtypeT | None:
        key = strip_null_bytes(key or "").strip()
        if not key:
            return None
        model_label = _subtype_scope_label(model_label)
        builtin = self.model.builtin_default(key)
        label, detail = builtin or (
            _humanize_key(key) if name is None else name,
            _humanize_key(key) if description is None else description,
        )
        subtype, _created = self.get_or_create(
            model_label=model_label,
            key=key,
            defaults={"name": label, "description": detail, "internal": internal, "created_by_id": created_by_id},
        )
        return subtype


def _humanize_key(value: str) -> str:
    """Return a human label from a subtype key."""

    return value.replace("_", " ").replace("-", " ").strip().capitalize() or "Message"


class MessageStarManager[MessageStarT: MessageStar](ProtectedManager[MessageStarT]):
    """Owns per-user Odoo-style starred message state."""

    def is_starred(self, message: Any, *, user: Any | None) -> bool:
        """Return whether ``user`` has starred ``message``."""

        user_id = getattr(user, "pk", None)
        if user_id is None:
            return False
        if hasattr(message, "_prefetched_objects_cache") and "stars" in message._prefetched_objects_cache:
            return any(star.user_id == user_id for star in message.stars.all())
        return self.model._base_manager.filter(message=message, user_id=user_id).exists()

    def prefetch_for(self, user: Any | None) -> models.Prefetch:
        """Prefetch ``user``'s own stars, so :meth:`is_starred` answers a page of messages in one query."""

        user_id = getattr(user, "pk", None)
        stars = self.model._base_manager.all()
        return models.Prefetch("stars", queryset=stars.filter(user_id=user_id) if user_id is not None else stars.none())

    def set_starred(self, message: Any, *, user: Any, starred: bool | None = None) -> bool:
        """Set or toggle ``user``'s star on ``message`` and return the new state."""

        user_id = getattr(user, "pk", None)
        if user_id is None:
            raise ValueError("A user is required to star a message.")
        with transaction.atomic():
            message = type(message)._base_manager.lock_if_supported().get(pk=message.pk)
            star = self.model._base_manager.lock_if_supported().filter(message=message, user_id=user_id).first()
            next_starred = not bool(star) if starred is None else bool(starred)
            if next_starred and star is None:
                self.model._base_manager.create(
                    message_id=message.pk,
                    user_id=user_id,
                    created_by_id=user_id,
                )
            elif not next_starred and star is not None:
                star.delete()
        return next_starred

    def unstar_all(self, *, user: Any) -> int:
        """Remove all stars owned by ``user``."""

        user_id = getattr(user, "pk", None)
        if user_id is None:
            return 0
        deleted, _details = self.model._base_manager.filter(user_id=user_id).delete()
        return int(deleted)


class ReactionManager[ReactionT: Reaction](ProtectedManager[ReactionT]):
    """Owns the attributed-reaction write — the row shape for a (message, handle, reaction).

    ``MessageManager.set_reaction`` is the user-keyed chatter toggle; this is the
    distinct attributed write the ``posts`` feed overlay lands for each external
    reactor. The row shape (fields + ``created_by`` default) lives here with the table
    owner so a producer batches through this owner instead of hand-rolling its own
    ``get_or_create``.
    """

    def attribute(self, reactions: Any, *, created_by_id: Any = None) -> int:
        """Land attributed reactions in one insert; return how many rows were built.

        ``reactions`` is an iterable of ``(message, handle, reaction)`` triples. One
        ``bulk_create`` inserts the batch, idempotent on the partial unique
        ``(message, handle, reaction)`` constraint via ``ignore_conflicts`` — so a
        re-sync re-landing the same reactions is a no-op — and every row carries the
        cleaned reaction content and the ``created_by`` attribution.
        """

        rows = [
            self.model(
                message_id=message.pk,
                handle_id=handle.pk,
                reaction=self.model.clean_reaction(reaction),
                created_by_id=created_by_id,
            )
            for message, handle, reaction in reactions
        ]
        if rows:
            self.bulk_create(rows, ignore_conflicts=True)
        return len(rows)


_MessageManagerBase = ProtectedManager.from_queryset(MessageQuerySet)


class MessageManager[MessageT: Message](_MessageManagerBase[MessageT]):
    """Owns idempotent ingest, insert deltas and state-change thread recounts."""

    def compose_reply(
        self, parent: Any, *, body: str, sender: Any, actor: Any,
        local: Mapping[str, Any] | None = None, idempotency_key: str | None = None,
        scheduled_at: datetime | None = None, queued: bool = False,
        admit: Callable[[Any], None] | None = None,
    ) -> Any:
        """Create an exact-once outbound reply under the channel's narrow reply grant.

        Hold facts are final at insert. Every retained reply advances its thread
        counter once; queueing does not change the count. Callers may contribute provenance, but not
        messaging's delivery state. ``admit(parent)`` checks domain eligibility
        only on insertion, after idempotency replay and fingerprint validation.
        Admission and replay run under actor even for an elevated caller.
        """

        if is_sudo():
            return Context().run(
                self.compose_reply, parent, body=body, sender=sender, actor=actor,
                local=local, idempotency_key=idempotency_key,
                scheduled_at=scheduled_at, queued=queued, admit=admit,
            )
        parent = parent.with_actor(actor)
        body = strip_null_bytes(body).strip()
        if not body:
            raise ValidationError({"body": "Reply text is required."})
        if parent.pk is None or parent.thread_id is None or parent.channel_id is None:
            raise ValidationError("A reply requires a saved parent, thread, and channel.")
        channel = parent.transport_channel(reason="messaging.reply.channel").with_actor(actor)
        if actor is None or not parent.has_access("read") or not channel.has_access("reply"):
            raise PermissionDenied("Comment read and channel reply access are required.")
        if sender is None or sender.pk is None or sender.platform != parent.platform:
            raise ValidationError({"sender": "The reply sender must use the comment's platform."})
        values = dict(local or {})
        if any(key.startswith("delivery_") for key in values):
            raise ValidationError({"local": "Delivery facts are owned by messaging."})
        claim_values = (
            {"parent": parent, "body": body, "sender": sender, "local": values}
            if idempotency_key is not None else None
        )
        actor_ref = to_subject_ref(actor)
        created_by_id = actor_user_id(actor_ref)
        claim_boundary = changeset(fresh=True) if idempotency_key is not None else nullcontext()
        with actor_context(actor), claim_boundary, transaction.atomic():
            if idempotency_key is not None:
                reply = changeset_model().objects.claim_created(
                    f"messaging.reply:{actor_ref}",
                    idempotency_key,
                    values=claim_values,
                    model=self.model,
                    unavailable=ValidationError(
                        {"idempotency_key": "The claimed reply is unavailable."},
                    ),
                )
                if reply is not None:
                    if reply.is_trashed:
                        raise ValidationError("This idempotency key belongs to a discarded reply.")
                    return reply.with_actor(actor)
            if admit is not None:
                admit(parent)
            with system_context(reason="messaging.reply.compose"):
                reply = self.create(
                    parent=parent, thread_id=parent.thread_id, channel_id=parent.channel_id, sender=sender,
                    platform=parent.platform, direction=self.model.Direction.OUTBOUND,
                    status=self.model.MessageStatus.QUEUED if queued else self.model.MessageStatus.DRAFT,
                    scheduled_at=scheduled_at, message_type=parent.message_type,
                    external_id=str(uuid4()), preview=body[: self.model.preview.field.max_length],
                    metadata=_bounded_message_metadata({"local": values}), created_by_id=created_by_id,
                )
                self._build_parts(
                    reply, ParsedPart(text=body), parent=None, position=0, created_by_id=created_by_id,
                )
                self.advance_thread(reply)
                if queued:
                    reply.deliver()
            if idempotency_key is not None:
                require_changeset().record_created(reply)
        return reply.with_actor(actor)

    def cancel_delivery(self, message: Any) -> None:
        """Disarm a removed outbound draft/queue under the same settlement row lock."""

        with system_context(reason="messaging.delivery.cancel"), transaction.atomic(using=self.db):
            row = self.sudo(reason="messaging.delivery.cancel").lock_if_supported().get(pk=message.pk)
            if row.direction == "outbound" and row.status != "uncertain" and (
                row.delivery_needs_reconciliation
                or (row.status in ("draft", "queued") and row.delivery_claim is not None)
            ):
                self._mark_delivery_uncertain(row)
            elif row.direction == "outbound" and row.status in ("draft", "queued"):
                row.status = self.model.MessageStatus.FAILED
                row.scheduled_at = row.delivery_lease_until = None
                row.metadata = {
                    **(row.metadata or {}), "local": {**row.local_metadata, "delivery_error": "Delivery cancelled."},
                }
                row.save(update_fields=("status", "scheduled_at", "delivery_lease_until", "metadata", "updated_at"))
            message.status = row.status
            message.scheduled_at = row.scheduled_at
            message.delivery_lease_until = row.delivery_lease_until
            message.delivery_possibly_accepted = row.delivery_possibly_accepted
            message.delivery_operator_only = row.delivery_operator_only
            message.metadata = row.metadata

    def claim_delivery(self, pk: Any, *, token: str, reconcile: bool = False) -> Any | None:
        """Commit an effect marker under the authoritative row lock before network I/O.

        A live claim cannot be replaced. Expiry becomes uncertain rather than
        permitting another publish. Only a reconcile-capable transport can claim
        an uncertain row; its new marker fences off any late prior settlement.
        """

        with system_context(reason="messaging.delivery.claim"), transaction.atomic():
            row = self.sudo(reason="messaging.delivery.claim").lock_if_supported().filter(
                pk=pk, external_id=token, status__in=("queued", "uncertain"),
            ).first()
            if row is None or row.delivery_blocked or row.delivery_held:
                return None
            now = timezone.now()
            if row.status == "queued" and row.delivery_claim is not None:
                if row.delivery_lease_until is not None and row.delivery_lease_until <= now:
                    self._mark_delivery_uncertain(row)
                return None
            if row.status == "uncertain" and (
                not reconcile or not row.delivery_possibly_accepted or row.delivery_operator_only
                or (row.scheduled_at is not None and row.scheduled_at > now)
                or (row.scheduled_at is None and row.delivery_lease_until is not None
                    and row.delivery_lease_until > now)
            ):
                return None
            if row.status == "uncertain":
                if self._reconciliation_policy(row)[0]:
                    self.model._base_manager.filter(pk=row.pk).update(
                        delivery_operator_only=True, delivery_lease_until=None, scheduled_at=None,
                    )
                    return None
                row.delivery_reconcile_attempts += 1
                self.model._base_manager.filter(pk=row.pk).update(
                    delivery_reconcile_attempts=row.delivery_reconcile_attempts,
                )
            try:
                row.validate_delivery()
            except ValidationError as error:
                self.record_delivery(pk, token=token, claim=row.delivery_claim, error=error, pre_send_refusal=True)
                return None
            row.delivery_claim = uuid4()
            if not row.delivery_possibly_accepted:
                row.delivery_started_at = now
            row.delivery_lease_until = now + task_claim_lease()
            row.scheduled_at = None
            fields: tuple[str, ...] = ("delivery_claim", "delivery_started_at", "delivery_lease_until", "scheduled_at")
            if row.status == "uncertain":
                self.model._base_manager.filter(pk=row.pk).update(**{field: getattr(row, field) for field in fields})
            else:
                row.save(update_fields=(*fields, "updated_at"))
            return row

    def _reconciliation_policy(self, row: Any) -> tuple[bool, timedelta]:
        """Read operator admission and visibility once, including lost recovery claims."""
        try:
            channel = row.transport_channel(reason="messaging.delivery.capability")
            capable = channel.can_reconcile_delivery()
            visibility_delay = channel.delivery_visibility_delay()
        except Exception:
            capable = False
            visibility_delay = timedelta()
        return not capable or row.delivery_reconcile_attempts >= MAX_RECONCILE_ATTEMPTS, visibility_delay

    def _set_reconciliation_horizon(self, row: Any, *, now: datetime) -> None:
        """Schedule idle recovery after visibility/backoff, with no live lease.

        Read the backend declaration without opening a transport. Claims use
        _reconciliation_policy directly; their live deadline overwrites no
        speculative idle horizon.
        """

        row.delivery_operator_only, visibility_delay = self._reconciliation_policy(row)
        delay = (
            RECONCILE_BACKOFF * 2 ** (row.delivery_reconcile_attempts - 1)
            if row.delivery_reconcile_attempts else timedelta()
        )
        row.delivery_lease_until = None
        row.scheduled_at = (
            None if row.delivery_operator_only else max(
                now + delay,
                row.delivery_started_at + visibility_delay if row.delivery_started_at is not None else now,
            )
        )

    def _mark_delivery_uncertain(self, row: Any, *, now: datetime | None = None) -> None:
        """Retain the effect fence and answer reservation after a lost settlement."""

        if row.delivery_claim is None and not row.delivery_needs_reconciliation:
            raise ValueError("An uncertain delivery requires a committed claim.")
        row.status = self.model.MessageStatus.UNCERTAIN
        row.delivery_possibly_accepted = True
        moment = now or timezone.now()
        if (row.delivery_claim is not None and row.delivery_lease_until is not None
                and row.delivery_lease_until > moment):
            # Trashing cannot supersede a provider call still protected by its lease.
            row.delivery_operator_only = self._reconciliation_policy(row)[0]
            row.scheduled_at = None
        else:
            self._set_reconciliation_horizon(row, now=moment)
        row.metadata = {**(row.metadata or {}), "local": {
            **row.local_metadata,
            "delivery_attempts": int(row.local_metadata.get("delivery_attempts", 0)) + 1,
            "delivery_error": "Delivery may have succeeded; reconcile or acknowledge before retrying.",
        }}
        row.save(update_fields=(
            "status", "scheduled_at", "delivery_lease_until", "delivery_possibly_accepted",
            "delivery_operator_only", "metadata", "updated_at",
        ))

    def confirm_delivery_absent(self, message: Any) -> None:
        """Fence an authoritative lookup's absence before permitting a fresh POST.

        Only the base reconciling backend calls this after a complete lookup.
        Refusals before that evidence cannot erase an earlier possible effect.
        The fresh attempt is an ordinary claimed send: QUEUED under the same
        claim and lease, so a lost worker expires into uncertainty that covers
        the new POST, and UNCERTAIN always means possibly accepted. It starts
        its own provider visibility window.
        """

        with system_context(reason="messaging.delivery.absent"), transaction.atomic():
            row = self.sudo(reason="messaging.delivery.absent").lock_if_supported().get(pk=message.pk)
            if row.delivery_claim != message.delivery_claim or row.external_id != message.external_id:
                raise DeliveryUncertain("The delivery claim changed during reconciliation.")
            row.status = self.model.MessageStatus.QUEUED
            row.delivery_possibly_accepted = False
            row.delivery_started_at = timezone.now()
            row.save(update_fields=("status", "delivery_possibly_accepted", "delivery_started_at", "updated_at"))
            message.status = row.status
            message.delivery_possibly_accepted = False
            message.delivery_started_at = row.delivery_started_at

    def record_delivery(
        self, pk: Any, *, token: str, claim: UUID | None, outcome: DeliveryOutcome | None = None,
        error: Exception | None = None, contention: bool = False, sent_at: datetime | None = None,
        pre_send_refusal: bool = False,
    ) -> str:
        """Settle acceptance or refusal once; contention does not consume an attempt.

        A collision is a successful publish with evidence, never a resend cue.
        The already ingested provider row owns the visible identity and payload;
        retain the local attempt as a trashed SENT audit row, with delivery_conflict
        pointing to that provider id. Its idempotency key and operator provenance
        remain available, but it contributes no second visible message or count.
        Acceptance racing with trash is still sent: removal cannot undo a publish.
        A settlement must carry its committed claim marker. Native transient
        IntegrationError refusals retry; unclassified post-send failures and
        DeliveryUncertain retain the parent answer reservation for acknowledgement.
        """

        provider_id = outcome.provider_id if outcome is not None else None
        with system_context(reason="messaging.delivery.settle"), transaction.atomic():
            row = self.sudo(reason="messaging.delivery.settle").lock_if_supported().filter(
                pk=pk, external_id=token, delivery_claim=claim, status__in=("queued", "uncertain"),
            ).first()
            if row is None:
                return "stale-delivery-token"
            now = sent_at or timezone.now()
            reconciling = row.delivery_needs_reconciliation
            local = row.local_metadata
            row.delivery_lease_until = None
            fields = [
                "status", "scheduled_at", "delivery_lease_until", "delivery_claim", "metadata", "updated_at",
                "delivery_possibly_accepted", "delivery_reconcile_attempts", "delivery_operator_only",
            ]
            # The SSRF policy refuses before any byte is sent: a definite refusal.
            uncertain_error = error is not None and (
                isinstance(error, DeliveryUncertain)
                or (
                    not pre_send_refusal
                    and not isinstance(error, (IntegrationError, AddressNotAllowedError))
                )
            )
            if reconciling and not (outcome is not None and outcome.accepted):
                status_changed = row.status != "uncertain"
                row.status = self.model.MessageStatus.UNCERTAIN
                self._set_reconciliation_horizon(row, now=now)
                local["delivery_error"] = (
                    safe_integration_failure(error).message if error
                    else "The prior delivery still needs reconciliation."
                )
                row.metadata = {**(row.metadata or {}), "local": local}
                # Recovery bookkeeping does not invalidate an operator's observed revision.
                if status_changed:
                    row.save(update_fields=fields)
                else:
                    self.model._base_manager.filter(pk=row.pk).update(**{
                        field: getattr(row, field) for field in fields if field != "updated_at"
                    })
                return "uncertain"
            if contention:
                row.status = self.model.MessageStatus.QUEUED
                row.delivery_claim = None
                row.scheduled_at = now + timedelta(seconds=30)
                row.delivery_lease_until = row.scheduled_at + timedelta(seconds=DELIVERY_TASK_EXPIRES)
                result = "retry"
            else:
                attempts = int(local.get("delivery_attempts", 0)) + (0 if reconciling else 1)
                local["delivery_attempts"] = attempts
                if error is not None and uncertain_error:
                    if row.delivery_claim is None:
                        raise ValueError("An uncertain delivery requires a committed claim.")
                    row.status = self.model.MessageStatus.UNCERTAIN
                    row.delivery_possibly_accepted = True
                    row.scheduled_at = None
                    self._set_reconciliation_horizon(row, now=now)
                    local["delivery_error"] = safe_integration_failure(error).message
                    result = "uncertain"
                elif (isinstance(error, IntegrationError) and error.transient
                        and attempts < MAX_DELIVERY_ATTEMPTS and not row.delivery_blocked):
                    row.status = self.model.MessageStatus.QUEUED
                    row.delivery_possibly_accepted = False
                    row.delivery_claim = None
                    delay = (error.retry_after if error.retry_after is not None
                             else timedelta(seconds=60 * 2 ** (attempts - 1)))
                    row.scheduled_at = now + max(delay, timedelta(seconds=1))
                    row.delivery_lease_until = row.scheduled_at + timedelta(seconds=DELIVERY_TASK_EXPIRES)
                    local["delivery_error"] = safe_integration_failure(error).message
                    result = "retry"
                else:
                    row.scheduled_at = None
                    accepted = outcome is not None and outcome.accepted
                    row.status = self.model.MessageStatus.SENT if accepted else self.model.MessageStatus.FAILED
                    row.delivery_possibly_accepted = False
                    result = "sent" if accepted else "failed"
                    if accepted:
                        local.pop("delivery_error", None)
                        row.sent_at = now
                        fields.append("sent_at")
                        if provider_id:
                            local["delivery_token"] = token
                            row.external_id = provider_id
                            fields.append("external_id")
                    else:
                        local["delivery_error"] = (
                            safe_integration_failure(error).message if error else "Delivery was declined."
                        )
            row.metadata = {**(row.metadata or {}), "local": local}
            try:
                with transaction.atomic():
                    row.save(update_fields=fields)
            except IntegrityError:
                collision = provider_id is not None and _external_id_annotated(
                    self.sudo(reason="messaging.delivery.collision"),
                ).filter(_external_id_q(provider_id), channel_id=row.channel_id).exclude(pk=pk).exists()
                if not collision:
                    raise
                row.external_id = token
                local["delivery_conflict"] = provider_id
                row.save(update_fields=[field for field in fields if field != "external_id"])
                # An operator may already have trashed the reconciled attempt.
                if not row.is_trashed:
                    row.trash(reason="Delivery settled onto the already ingested provider message.", recount=False)
            if result == "sent" and row.thread_id:
                row._recount_thread()
            return result

    def resolve_uncertain_delivery(
        self, message: Any, *, actor: Any, expected_revision: int, retry: bool,
    ) -> Any:
        """Acknowledge sent or explicitly retry under the same grant as held replies.

        Retry asserts that the operator accepts the repeat-effect risk. It clears
        the old fence and supersedes late settlements, preserving the first claim
        time so a reconcile-capable backend still searches the original attempt.
        FAILED rows can also be retried; retry resets both attempt budgets.
        """

        if actor is None:
            raise PermissionDenied("Authentication required.")
        with transaction.atomic():
            row = self.with_actor(actor).with_action("send_held").lock_if_supported().get(pk=message.pk)
            row.require_revision(expected_revision)
            if (row.status == "uncertain" and row.scheduled_at is None
                    and row.delivery_lease_until is not None and row.delivery_lease_until > timezone.now()):
                raise DeliveryReconciliationBusy()
            allowed = ("uncertain", "failed") if retry else ("uncertain",)
            if row.status not in allowed or row.delivery_blocked:
                raise ValidationError(
                    "Retry requires a retained failed or uncertain delivery; acknowledgement requires uncertainty."
                )
            with system_context(reason="messaging.delivery.acknowledge"):
                row.acknowledged_by_id = actor_user_id(to_subject_ref(actor))
                if retry:
                    if row.is_trashed:
                        row.restore()
                    row.delivery_reconcile_attempts = 0
                    row.delivery_operator_only = False
                    row.metadata = {**(row.metadata or {}), "local": {**row.local_metadata, "delivery_attempts": 0}}
                    row.status = self.model.MessageStatus.DRAFT
                    row.delivery_claim = None
                    row.scheduled_at = row.delivery_lease_until = None
                    row.save(update_fields=(
                        "status", "delivery_claim", "scheduled_at", "delivery_lease_until", "updated_at",
                        "acknowledged_by", "metadata", "delivery_reconcile_attempts", "delivery_operator_only",
                    ))
                    row.deliver()
                else:
                    row.save(update_fields=("acknowledged_by", "updated_at"))
                    self.record_delivery(
                        row.pk, token=row.external_id, claim=row.delivery_claim, outcome=DeliveryOutcome(accepted=True),
                    )
                    row.refresh_from_db()
        return row.with_actor(actor)

    def send_held_drafts(self, selection: list[ActionSelectionInput], *, actor: Any) -> list[ActionResult]:
        """Send an operator's revision-checked selection through many_actions."""

        return self._act_on_held_drafts(selection, actor=actor, discard=False)

    def discard_held_drafts(self, selection: list[ActionSelectionInput], *, actor: Any) -> list[ActionResult]:
        """Discard an operator's revision-checked selection and recount threads once."""

        return self._act_on_held_drafts(selection, actor=actor, discard=True)

    def _act_on_held_drafts(
        self, selection: list[ActionSelectionInput], *, actor: Any, discard: bool,
    ) -> list[ActionResult]:
        validate_action_selection(selection)
        if actor is None:
            raise PermissionDenied("Authentication required.")
        with transaction.atomic():
            # send_held is precisely channel write, including for discard. The
            # identity owner decodes once and performs one scoped pk__in query.
            scoped = (
                self.with_actor(actor).with_action("send_held").inbox().lock_if_supported().order_by("pk")
            )
            rows = instances_from_public_ids(self.model, [str(item.id) for item in selection], queryset=scoped)
            threads = []
            if discard:
                thread_model = apps.get_model("messaging", "Thread")
                with system_context(reason="messaging.held.recount"):
                    threads = list(thread_model._base_manager.lock_if_supported().filter(
                        pk__in={row.thread_id for row in rows.values() if row.thread_id is not None},
                    ).order_by("pk"))
            def run(item: ActionSelectionInput) -> ActionResult:
                row = rows.get(str(item.id))
                if row is None:
                    raise ValidationError("A selected held draft is unavailable.")
                row.require_revision(item.expected_revision)
                row.validate_held_draft()
                with system_context(reason="messaging.held.discard" if discard else "messaging.held.send"):
                    if discard:
                        row.trash(reason="Discarded held draft.", recount=False)
                    else:
                        row.scheduled_at = None
                        row.save(update_fields=("scheduled_at", "updated_at"))
                        row.deliver()
                return ActionResult(ok=True, message="Held draft discarded." if discard else "Held draft queued.")
            results = many_actions(selection, run)
            with system_context(reason="messaging.held.recount"):
                for thread in threads:
                    self._recount_thread(thread)
            return results

    def release_held_messages(self, *, now: datetime | None = None, limit: int = 200) -> int:
        """SQL-scoped skip-locked release, with a savepoint for every row."""

        moment = now or timezone.now()
        if not 1 <= limit <= 1000:
            raise ValueError("Release limit must be between 1 and 1000.")
        count = 0
        with system_context(reason="messaging.delivery.release"), transaction.atomic():
            expired = self.sudo(reason="messaging.delivery.reap").expired_delivery_claims(now=moment).lock_if_supported(
                skip_locked=True,
            ).order_by("pk")[:limit]
            for row in expired:
                self._mark_delivery_uncertain(row, now=moment)
            uncertain = self.sudo(reason="messaging.delivery.reconcile").filter(
                status="uncertain", delivery_operator_only=False, delivery_possibly_accepted=True,
            ).filter(
                models.Q(delivery_lease_until__isnull=True) | models.Q(delivery_lease_until__lte=moment),
            ).filter(models.Q(scheduled_at__isnull=True) | models.Q(scheduled_at__lte=moment))
            for row in uncertain.lock_if_supported(skip_locked=True).order_by("pk")[:limit]:
                try:
                    with transaction.atomic():
                        count += row.deliver()
                except Exception as error:
                    logger.warning("Delivery reconciliation unavailable (%s).", type(error).__name__)
                if count >= limit:
                    break
            rows = self.sudo(reason="messaging.delivery.release").due_deliveries(now=moment).lock_if_supported(
                skip_locked=True,
            ).order_by("pk")[:limit]
            for row in rows:
                try:
                    with transaction.atomic():
                        count += row.deliver()
                except ValidationError as error:
                    row.status = self.model.MessageStatus.QUEUED
                    row.save(update_fields=("status", "updated_at"))
                    self.record_delivery(
                        row.pk, token=row.external_id, claim=row.delivery_claim, error=error, pre_send_refusal=True,
                    )
        return count


    def post_to_thread(
        self,
        thread: Any,
        *,
        body: str,
        created_by_id: Any = None,
        attachment: Any | None = None,
        record: Any | None = None,
        attachments: tuple[Any, ...] = (),
        message_type: Message.MessageKind | None = None,
        subtype_key: str = "comment",
        subtype_model_label: str = "",
        parent: Any | None = None,
        recipient_user_ids: tuple[Any, ...] = (),
        autofollow_author: bool = False,
        autofollow_recipients: bool = False,
        idempotency_key: str | None = None,
        idempotency_actor: SubjectRef | None = None,
    ) -> Any:
        """Create an internal user-authored message in ``thread`` and bump thread counters.

        ``message_type`` defaults to :attr:`Message.MessageKind.COMMENT`; the enum is
        the single source of truth for the stored kind, so ``None`` resolves to it here.
        A chatter message carries no title part — the thread's title fragment labels
        the conversation. The poster's own read receipt advances to the new message,
        so an author never sees their own post as unread.

        Idempotency belongs to the initiating REBAC actor, including non-user actors;
        ``created_by_id`` remains attribution. An exact replay returns the original
        message without repeating posting side effects. Changed posting inputs
        raise ``CreationKeyConflict``. Idempotent calls require an explicit or ambient
        non-anonymous actor.
        """

        body = strip_null_bytes(body or "").strip()
        kind = strip_null_bytes(message_type or self.model.MessageKind.COMMENT)
        claim_values: dict[str, object] = {}
        actor = None
        if idempotency_key is not None:
            actor = idempotency_actor or current_actor()
            if actor is None or is_anonymous_actor(actor):
                raise ValueError("An idempotent message requires an actor.")
            claim_values = {
                "thread": str(thread.pk),
                "attachment": str(attachment.pk) if attachment is not None else None,
                "body": body,
                "attachments": [str(file.pk) for file in attachments],
                "message_type": kind,
                "subtype_key": strip_null_bytes(subtype_key or "").strip(),
                "parent": str(parent.pk) if parent is not None else None,
                "recipient_user_ids": sorted({str(user_id) for user_id in recipient_user_ids}),
                "autofollow_recipients": autofollow_recipients,
            }
        if not body and not attachments:
            raise ValueError("Message body or attachment is required.")
        if parent is not None and not thread.member_threads().filter(pk=parent.thread_id).exists():
            raise ValueError("Parent message does not belong to this thread.")
        if (autofollow_author or autofollow_recipients) and (record is None or attachment is None):
            raise ValueError("Autofollow requires the record and its thread attachment.")
        using = self._db or thread._state.db or self.db
        messages = self.db_manager(using)
        part_model = apps.get_model("messaging", "Part")
        fragment_model = apps.get_model("messaging", "Fragment")
        notification_model = apps.get_model("messaging", "ThreadNotification")
        sent_at = timezone.now()
        claim_boundary = changeset(fresh=True, using=using) if idempotency_key is not None else nullcontext()
        with database_context(using), claim_boundary, transaction.atomic(using=using):
            if idempotency_key is not None:
                message = changeset_model().objects.db_manager(using).claim_created(
                    f"messaging.post:{actor}",
                    idempotency_key,
                    values=claim_values,
                    model=self.model,
                    unavailable=ValidationError(
                        {"idempotency_key": "The claimed message is unavailable."},
                    ),
                )
                if message is not None:
                    if message.is_trashed:
                        raise ValidationError("This idempotency key belongs to a discarded message.")
                    return message
            subtype = apps.get_model("messaging", "MessageSubtype").objects.db_manager(using).resolve(
                subtype_key,
                model_label=subtype_model_label,
                created_by_id=created_by_id,
            )

            sender = None
            if created_by_id is not None:
                user_model = self.model.created_by.field.related_model
                sender = apps.get_model("parties", "Handle").objects.db_manager(using).for_user(
                    user_model._base_manager.using(using).get(pk=created_by_id)
                )
            message = messages.create(
                thread_id=thread.pk,
                sender=sender,
                platform=thread.platform,
                direction=self.model.Direction.INTERNAL,
                status=self.model.MessageStatus.SENT,
                message_type=kind,
                subtype_id=subtype.pk if subtype is not None else None,
                parent_id=parent.pk if parent is not None else None,
                preview=body[:280],
                sent_at=sent_at,
                created_by_id=created_by_id,
            )
            position = 0
            if body:
                fragment = fragment_model.objects.db_manager(using).upsert(
                    text=body, kind=fragment_model.FragmentKind.PARAGRAPH, created_by_id=created_by_id
                )
                part_model.objects.db_manager(using).create(
                    message_id=message.pk,
                    position=position,
                    type="text/plain",
                    disposition=part_model.Disposition.INLINE,
                    role=part_model.PartRole.BODY,
                    fragment_id=fragment.pk if fragment is not None else None,
                    created_by_id=created_by_id,
                )
                position += 1
            for file in attachments:
                part_model.objects.db_manager(using).attach_file(
                    message, file, position=position,
                    name=file.filename or "attachment", created_by_id=created_by_id,
                )
                position += 1
            notification_model.objects.db_manager(using).fanout_for_message(
                message,
                attachment=attachment,
                created_by_id=created_by_id,
                recipient_user_ids=recipient_user_ids,
            )
            follower_model = apps.get_model("messaging", "ThreadFollower")
            if attachment is not None and (autofollow_author or autofollow_recipients):
                if record is None:
                    raise ValueError("An attached record is required for automatic follows.")
                # The record already authorized the post. Keep its follow bookkeeping
                # in the same transaction, after fan-out, and never repeat it on replay.
                with system_context(reason="messaging.autofollow"):
                    candidates = set(recipient_user_ids if autofollow_recipients else ())
                    if autofollow_author and created_by_id is not None:
                        candidates.add(created_by_id)
                    accounts = list(get_user_model()._base_manager.using(using).filter(pk__in=candidates))
                    readers = record.thread_reader_ids(account for account in accounts if account.kind == "person")
                    for account in accounts:
                        if account.kind != "person" or account.pk in readers:
                            follower_model.objects.db_manager(using).subscribe(
                                record, user=account, role=attachment.role,
                            )
            if created_by_id is not None:
                follower_model.objects.db_manager(using).mark_read_up_to(thread, user_id=created_by_id, message=message)
            with system_context(reason="messaging.thread.count"):
                self._advance_thread(thread, message.sent_at)
            type(message).thread.field.set_cached_value(message, thread)
            message_ingested.send(sender=self.model, instance=message)
            if idempotency_key is not None:
                require_changeset().record_created(message)
        return message

    def set_reaction(self, message: Any, *, reaction: str, action: str = "toggle", user: Any) -> Any:
        """Add, remove, or toggle the current user's reaction on ``message``."""

        reaction_model = apps.get_model("messaging", "Reaction")
        reaction = reaction_model.clean_reaction(reaction)
        action = strip_null_bytes(action or "toggle").strip().lower()
        if action not in {"add", "remove", "toggle"}:
            raise ValueError("Reaction action must be add, remove, or toggle.")
        handle = apps.get_model("parties", "Handle").objects.for_user(user)
        with transaction.atomic():
            message = type(message)._base_manager.lock_if_supported().get(pk=message.pk)
            queryset = reaction_model._base_manager.lock_if_supported().filter(
                message=message,
                handle=handle,
                reaction=reaction,
            )
            exists = queryset.exists()
            should_add = action == "add" or (action == "toggle" and not exists)
            if should_add and not exists:
                reaction_model._base_manager.create(
                    message_id=message.pk,
                    handle_id=handle.pk,
                    reaction=reaction,
                    created_by_id=user.pk,
                )
            elif action == "remove" or (action == "toggle" and exists):
                queryset.delete()
        return message

    def update_content(self, message: Any, *, body: str, edited_by_id: Any = None) -> Any:
        """Update a user-authored comment body, preserving Odoo's edit guardrails.

        An edit is data, not a shadow row: the replaced text survives as immutable
        content-addressed fragments, so the ``edit_history`` entry records only the
        prior fragment hashes (newest first) alongside who edited and when.
        """

        body = strip_null_bytes(body or "").strip()
        if not body:
            raise ValueError("Message body is required.")
        with transaction.atomic():
            message = (
                type(message)
                ._base_manager.lock_if_supported()
                .get(pk=message.pk)
            )
            edit_error = message.content_edit_error()
            if edit_error is not None:
                raise ValueError(edit_error)
            record = message.threaded_record()
            if record is None or not message.can_change_comment(
                post_access=record.can_post(),
                moderate_access=record.can_moderate(),
                actor_id=actor_user_id(current_actor()),
            ):
                raise PermissionDenied("Editing requires author or moderator access to the record.")
            with system_context(reason="messaging.comment.edit"):
                part_model = apps.get_model("messaging", "Part")
                fragment_model = apps.get_model("messaging", "Fragment")
                fragment = fragment_model.objects.upsert(
                    text=body, kind=fragment_model.FragmentKind.PARAGRAPH, created_by_id=edited_by_id
                )
                text_parts = list(
                    part_model._base_manager.lock_if_supported()
                    .filter(message=message, role=part_model.PartRole.BODY, fragment__isnull=False)
                    .select_related("fragment")
                    .order_by("position", "pk")
                )
                prior_hashes = [
                    prior_fragment.hash for part in text_parts if (prior_fragment := part.fragment) is not None
                ]
                if text_parts:
                    body_part = text_parts[0]
                    body_part.fragment = fragment
                    body_part.type = "text/plain"
                    body_part.disposition = part_model.Disposition.INLINE
                    body_part.name = ""
                    body_part.save(update_fields=("fragment", "type", "disposition", "name", "updated_at"))
                    part_model._base_manager.filter(pk__in=[part.pk for part in text_parts[1:]]).delete()
                else:
                    part_model._base_manager.filter(message=message).update(position=models.F("position") + 1)
                    part_model._base_manager.create(
                        message_id=message.pk,
                        position=0,
                        type="text/plain",
                        disposition=part_model.Disposition.INLINE,
                        role=part_model.PartRole.BODY,
                        fragment_id=fragment.pk if fragment is not None else None,
                        created_by_id=edited_by_id,
                    )
                message.edit_history = [
                    _edit_history_entry(edited_by_id=edited_by_id, prev_fragment_hashes=prior_hashes),
                    *(message.edit_history or []),
                ]
                message.preview = body[:280]
                message.status = self.model.MessageStatus.EDITED
                message.save(update_fields=("preview", "status", "edit_history", "updated_at"))
                return message

    def unlink_from_thread(self, message: Any, *, thread: Any) -> Any:
        """Delete ``message`` from ``thread`` and repair thread denormalisations."""

        with transaction.atomic():
            message = (
                type(message)
                ._base_manager.lock_if_supported()
                .get(pk=message.pk)
            )
            if not thread.member_threads().filter(pk=message.thread_id).exists():
                raise ValueError("Message does not belong to this thread.")
            delete_error = message.delete_error()
            if delete_error is not None:
                raise ValueError(delete_error)
            record = message.threaded_record()
            if record is None or not message.can_change_comment(
                post_access=record.can_post(),
                moderate_access=record.can_moderate(),
                actor_id=actor_user_id(current_actor()),
            ):
                raise PermissionDenied("Deleting requires author or moderator access to the record.")
            with system_context(reason="messaging.comment.delete"):
                thread_model = type(thread)
                physical_thread = thread_model._base_manager.lock_if_supported().get(pk=message.thread_id)
                message.delete()
                self._recount_thread(physical_thread)
        return thread

    def trash_in_thread(self, message: Any, *, thread: Any, reason: str = "") -> Any:
        """Trash a comment of ``thread`` as its record's moderator and recount the thread.

        The moderator, not the elevated write, is stamped as the acting user.
        """

        return self._moderate_in_thread(message, thread=thread, trash=True, reason=reason)

    def restore_in_thread(self, message: Any, *, thread: Any) -> Any:
        """Restore a trashed comment of ``thread`` as its record's moderator and recount."""

        return self._moderate_in_thread(message, thread=thread, trash=False)

    def in_record(
        self, record: Any, message_id: str, *, role: str = "chatter", trashed: bool = False,
    ) -> MessageT | None:
        """Return one message of ``record``'s thread by public id, in or out of the trash.

        Elevated structural lookup, like the transcript it serves: the caller has
        gated the record's read and, for a trashed message, its moderation.
        """

        attachment = apps.get_model("messaging", "ThreadAttachment").objects.for_record(record, role=role)
        if attachment is None:
            return None
        return (
            self.sudo(reason="messaging.message.in_record")
            .for_thread(attachment.thread)
            .filter(is_trashed=trashed, **self.model.public_id_lookup(str(message_id)))
            .first()
        )

    def _moderate_in_thread(self, message: Any, *, thread: Any, trash: bool, reason: str = "") -> Any:
        """Lock, re-authorize and trash or restore one comment, then recount its thread."""

        with transaction.atomic():
            message = (
                type(message)
                ._base_manager.select_for_update()
                .get(pk=message.pk)
            )
            if not thread.member_threads().filter(pk=message.thread_id).exists():
                raise ValueError("Message does not belong to this record thread.")
            if trash and (error := message.trash_error()):
                raise ValueError(error)
            record = message.threaded_record()
            if record is None or not record.can_moderate():
                raise PermissionDenied("Removing or restoring comments requires moderator access to the record.")
            with system_context(reason="messaging.comment.trash" if trash else "messaging.comment.restore"):
                if trash:
                    message.trash(reason=reason)
                else:
                    message.restore()
        return message

    def _recount_thread(self, thread: Any) -> None:
        """Recompute a thread's denormalised counters from its surviving messages.

        Loss, restoration, re-threading and accepted settlement use this owner.
        All retained rows count; insert paths use an F() increment instead.
        Call within a transaction; this owner locks the
        thread before reading the aggregate, including for explicit threads.
        """

        type(thread)._base_manager.select_for_update().values_list("pk", flat=True).get(pk=thread.pk)
        untrashed = self.model.counted_in_thread_q()
        summary = self.model._base_manager.filter(thread=thread).aggregate(
            total=models.Count("pk"),
            count=models.Count("pk", filter=untrashed),
            last_sent_at=models.Max("sent_at", filter=untrashed),
        )
        count = int(summary["count"] or 0)
        if not summary["total"] and not thread.is_record_attached() and not thread.retained():
            # An emptied inbox thread is a husk — every message re-resolved
            # elsewhere. Deleting it keeps the thread list free of zero-message
            # rows; a record chatter thread stays (it exists before its first post),
            # and so does a thread still holding trashed messages.
            thread.delete()
            return
        thread.message_count = count
        thread.last_message_at = summary["last_sent_at"]
        thread.save(update_fields=("message_count", "last_message_at", "updated_at"))

    @staticmethod
    def _thread_advance_values(sent_at: Any) -> dict[str, Any]:
        """Monotonic count/time deltas shared by ingest and streamed chatter."""

        values: dict[str, Any] = {"message_count": models.F("message_count") + 1}
        if sent_at is not None:
            values["last_message_at"] = Greatest(Coalesce(models.F("last_message_at"), sent_at), sent_at)
        return values

    def advance_thread(self, message: Any) -> None:
        """Advance a newly inserted message once, using its retained-row and sent-time policy.

        Producers call this inside the insertion transaction after composing the
        envelope and parts. Settlement/re-threading uses the recount owner instead.
        """

        if message.thread_id is None:
            return
        counted = self.model._base_manager.filter(self.model.counted_in_thread_q(), pk=message.pk)
        apps.get_model("messaging", "Thread")._base_manager.filter(
            pk=message.thread_id,
        ).filter(models.Exists(counted)).update(
            **self._thread_advance_values(message.sent_at), updated_at=timezone.now(),
        )

    def _advance_thread(self, thread: Any, sent_at: Any) -> None:
        """Stream a newly posted record comment through the native thread save hook."""

        values = self._thread_advance_values(sent_at)
        for field, expression in values.items():
            setattr(thread, field, expression)
        with system_context(reason="messaging.thread.bump"):
            thread.save(using=thread._state.db, update_fields=tuple(values))

    def correct_source_thread(
        self, message: MessageT, *, channel: Channel, expected_thread: Thread, thread: Thread,
    ) -> MessageT:
        """Compare and set a source message's thread without rebuilding its content.

        Both saved threads and the non-local envelope must belong to the admitted
        channel. The message lock fences stale corrections; native saves retain
        changeset/history and both thread counters are recomputed in that transaction.
        This source-ingestion command grants no caller authority of its own.
        """
        thread_model = apps.get_model("messaging", "Thread")
        if any(not isinstance(candidate, thread_model) or candidate.pk is None
               for candidate in (expected_thread, thread)):
            raise ValueError("Source thread correction requires saved previous and explicit target threads.")
        with transaction.atomic():
            message = self.model._base_manager.select_for_update(of=("self",)).get(pk=message.pk)
            if message.channel_id != channel.pk or message.thread_id != expected_thread.pk:
                raise ValueError("Source message thread changed after the correction was prepared.")
            if message.is_locally_written:
                raise ValueError("Source correction cannot move a locally authored message.")
            channels = dict(thread_model._base_manager.select_for_update().filter(
                pk__in={expected_thread.pk, thread.pk},
            ).order_by("pk").values_list("pk", "channel_id"))
            if any(channels.get(pk) != channel.pk for pk in (expected_thread.pk, thread.pk)):
                raise ValueError("Source thread correction must stay within its admitted channel.")
            if message.thread_id == thread.pk:
                return message
            message.thread = thread
            parent = message.parent
            if parent is not None and parent.thread_id != thread.pk:
                message.parent = None
            message.save(update_fields=("thread", "parent", "updated_at"))
            apps.get_model("messaging", "Participant")._base_manager.filter(message=message).update(thread=thread)
            self._recount_thread(expected_thread)
            self._recount_thread(thread)
            return message

    def ingest(
        self,
        parsed_messages: list[ParsedMessage],
        *,
        channel: Any,
        created_by_id: Any = None,
        modality: Any = None,
        visibility: Any = None,
        quote_edges: bool = True,
        explicit_thread: Any = None,
        expected_thread: Any = None,
        historical: bool = False,
        after_landing: Callable[[Any], None] | None = None,
    ) -> list[Any]:
        """Upsert each parsed message into a thread with its parts/participants/edges.

        Returns the landed :class:`~angee.messaging.models.Message` rows (a caller
        wanting the count takes ``len(...)``) so an overlay — a public-feed engagement
        pass — reuses the rows this write already resolved instead of re-querying them
        by external id.

        Idempotent on ``(channel, external_id)``; null bytes stripped; thread
        counters incremented with F() deltas on insertion and recounted on loss. ``in_reply_to`` lands twice,
        each through its owner: thread membership via :meth:`ThreadManager.resolve` and the
        single-parent reply pointer via :meth:`_resolve_reply_parent` onto
        ``Message.parent``. ``modality``/``visibility`` land each resolved
        thread under a non-email :class:`~angee.messaging.models.Thread.Modality` /
        :class:`~angee.messaging.models.Thread.Visibility` (a public feed passes
        ``PUBLIC_THREAD``/``PUBLIC``); each defaults to the private email-thread shape.
        The functional :class:`~angee.messaging.models.Message.MessageKind` is decided
        here, from the structural facts, never by the producer: content in a
        record-attached thread or ``PUBLIC_THREAD`` is a ``COMMENT``, a message whose
        source names its conversation (``ParsedThread`` or ``explicit_thread``) is
        ``CHAT``, and everything else is ``EMAIL``. The same act keeps its kind across
        backends. ``quote_edges`` runs the RFC-5322 quotation
        builder — email's shared-fragment graph — and defaults on; a non-email producer
        whose short shared text would otherwise mint spurious ``quote`` edges passes
        ``quote_edges=False``. The externally controlled metadata envelope is rejected
        above 512 KiB of canonical UTF-8 JSON so the lossless column remains
        deterministically bounded.

        ``explicit_thread`` binds source record chatter to an already resolved,
        saved thread, avoiding email subject/reply heuristics. Replaying a channel's
        external ID into a different explicit thread is rejected unless
        ``expected_thread`` names its exact previous source thread. That compare
        and set runs under the message lock, requires both threads to belong to
        this channel, and never moves a locally authored message. ``historical``
        suppresses the live message event and party-suggestion side effects while
        retaining original timestamps, content history and thread counters.
        ``after_landing`` writes an addon's overlay and moderation before counters
        and live events; it also runs on unchanged observations without re-emitting.
        """

        created_by_id = created_by_id if created_by_id is not None else channel.owner_id
        thread_model = apps.get_model("messaging", "Thread")
        if explicit_thread is not None:
            if not isinstance(explicit_thread, thread_model) or explicit_thread.pk is None:
                raise ValueError("Explicit ingestion requires a saved messaging Thread.")
            if explicit_thread.channel_id not in (None, channel.pk):
                raise ValueError("Explicit thread belongs to a different source channel.")
        if expected_thread is not None and (
            explicit_thread is None or not isinstance(expected_thread, thread_model) or expected_thread.pk is None
        ):
            raise ValueError("Source thread correction requires saved previous and explicit target threads.")
        ingested: list[Any] = []
        unresolved_handles: dict[Any, Any] = {}
        for parsed in parsed_messages:
            if not parsed.external_id:
                continue
            # The sync entrypoint (a stream cycle, a scheduled pull) owns the changeset.
            with transaction.atomic():
                message, handles = self._ingest_one(
                    parsed,
                    channel=channel,
                    created_by_id=created_by_id,
                    thread_model=thread_model,
                    modality=modality,
                    visibility=visibility,
                    explicit_thread=explicit_thread,
                    expected_thread=expected_thread,
                    historical=historical,
                    after_landing=after_landing,
                )
                if message.message_type == "email":
                    # Robust: identity evidence never fails a committed envelope or the rest of the batch.
                    transaction.on_commit(
                        partial(observe_email_identity, message), using=self.db, robust=True,
                    )
                ingested.append(message)
                for handle in handles:
                    if handle.party_id is None:
                        unresolved_handles[handle.pk] = handle
        # Quotation runs after the whole batch lands, so a message quoting a later
        # one in the same batch still links (an inline pass would miss it). It is the
        # email graph, so a non-email producer skips it via ``quote_edges=False``.
        if quote_edges:
            self.resolve_ingest_edges(ingested)
        # Revisit unresolved envelopes after each committed batch so later directory
        # evidence can resolve an older sender without coupling it to first contact.
        if unresolved_handles and not historical:
            handles = tuple(unresolved_handles.values())

            def suggest_parties() -> None:
                try:
                    parties = apps.get_model("parties", "Party").objects
                except Exception:
                    logger.exception("Could not initialize messaging party suggestions")
                    return
                for handle in handles:
                    try:
                        with (
                            changeset(fresh=True, activity="system", change_reason="messaging.ingest.suggest_party"),
                            system_context(reason="messaging.ingest.suggest_party"),
                        ):
                            parties.suggest_for(handle)
                    except Exception:
                        logger.exception(
                            "Could not suggest a party for messaging handle %s",
                            handle.pk,
                        )

            transaction.on_commit(suggest_parties)
        return ingested

    def resolve_ingest_edges(self, messages: Sequence[Any]) -> None:
        """Resolve quotation edges after every successful message in a page has landed."""

        edges = apps.get_model("messaging", "MessageEdge").objects
        for message in messages:
            edges.create_for_message(message)

    def _ingest_one(
        self,
        parsed: ParsedMessage,
        *,
        channel: Any,
        created_by_id: Any,
        thread_model: Any,
        modality: Any = None,
        visibility: Any = None,
        explicit_thread: Any = None,
        expected_thread: Any = None,
        historical: bool = False,
        after_landing: Callable[[Any], None] | None = None,
    ) -> Any:
        handle_model = apps.get_model("parties", "Handle")
        part_model = apps.get_model("messaging", "Part")
        envelope_metadata = self.model.ingest_metadata(_bounded_message_metadata(parsed.metadata))
        # Resolve identity before provider facts can create a thread or sender:
        # an outbound echo belongs to the locally authored envelope.
        prior = (
            _external_id_annotated(self.model._base_manager)
            .filter(_external_id_q(parsed.external_id), channel=channel)
            .select_related("thread")
            .first()
        )
        received_at = parsed.received_at if parsed.received_at is not None else prior.received_at if prior else None
        content_hash = _parsed_sync_hash(
            parsed, channel_id=channel.pk, metadata=envelope_metadata, received_at=received_at,
        )
        unchanged = (
            prior is not None and prior.thread_id is not None
            and (explicit_thread is not None or prior.thread.channel_id == channel.pk)
            and (explicit_thread is None or prior.thread_id == explicit_thread.pk)
            and (prior.metadata or {}).get(_SYNC_HASH_KEY) == content_hash
        )
        if unchanged and not prior.is_locally_written and explicit_thread is None and parsed.thread is None:
            referenced = thread_model.objects.referenced_thread(
                platform=parsed.platform, channel=channel, in_reply_to=parsed.in_reply_to, references=parsed.references,
            )
            unchanged = referenced is None or referenced.pk == prior.thread_id
        if expected_thread is None and unchanged and (
            prior.is_locally_written or not parsed.in_reply_to or prior.parent_id is not None
            or self._resolve_reply_parent(parsed, channel=channel, explicit_thread=explicit_thread) is None
        ):
            if after_landing is not None:
                after_landing(prior)
            return prior, ()
        # Lock Message before any thread resolution, but only for a mutation.
        if prior is not None:
            # Lock only the message row: the thread is a nullable (outer) join.
            prior = self.model._base_manager.select_for_update(of=("self",)).select_related("thread").get(pk=prior.pk)
        source_thread_changed = (
            expected_thread is not None and prior is not None and prior.thread_id != explicit_thread.pk
        )
        if expected_thread is not None:
            if prior is None:
                raise ValueError("Source message thread changed after the correction was prepared.")
        created = prior is None
        if created:
            try:
                # Reserve the provider identity before resolving/locking Thread.
                # The transaction exposes only the completed envelope, and the
                # reservation must not publish an incomplete change event.
                with transaction.atomic(), mute_changes():
                    message = self.create(
                        external_id=parsed.external_id, channel=channel, created_by_id=created_by_id,
                        platform=parsed.platform, direction=parsed.direction,
                    )
            except IntegrityError:
                prior = _external_id_annotated(self.model._base_manager).filter(
                    _external_id_q(parsed.external_id), channel=channel,
                ).select_for_update(of=("self",)).select_related("thread").first()
                if prior is None:
                    raise
                created = False
        authored_message = prior if prior is not None and prior.is_locally_written else None
        if authored_message is not None:
            thread = authored_message.thread
        elif explicit_thread is not None:
            thread = explicit_thread
        else:
            thread = thread_model.objects.resolve(
                platform=parsed.platform,
                channel=channel,
                subject=parsed.subject,
                in_reply_to=parsed.in_reply_to,
                references=parsed.references,
                message_external_id=parsed.external_id,
                created_by_id=created_by_id,
                modality=modality,
                visibility=visibility,
                thread=parsed.thread,
            )
        sender = None
        if parsed.sender is not None and authored_message is None:
            sender = handle_model.objects.upsert(
                platform=parsed.sender.platform,
                value=parsed.sender.value,
                created_by_id=created_by_id,
                display_name=parsed.sender.display_name,
                external_id=parsed.sender.external_id,
                metadata=parsed.sender.metadata,
            )
        if (
            prior is not None
            and (expected_thread is not None or prior.thread_id == (thread.pk if thread is not None else None))
            and (prior.metadata or {}).get(_SYNC_HASH_KEY) == content_hash
        ):
            # Idempotent re-sync into the same thread with identical content: nothing
            # to rebuild — skipping the part rebuild avoids churning Part primary keys,
            # re-upserting Fragments, and minting a spurious edit-history entry. One
            # precise exception: a reply that landed before its parent keeps NULL, so
            # the re-sync backfills the pointer once its parent exists (this is how a
            # resumed backup import heals reply order across batches).
            message = authored_message or self.model._base_manager.select_related("thread").get(pk=prior.pk)
            if expected_thread is not None:
                message = self.correct_source_thread(message, channel=channel, expected_thread=expected_thread,
                                                     thread=explicit_thread)
            if authored_message is None and parsed.in_reply_to and prior.parent_id is None:
                parent = self._resolve_reply_parent(parsed, channel=channel, explicit_thread=explicit_thread)
                if parent is not None:
                    message = self.model._base_manager.select_for_update().get(pk=prior.pk)
                    message.parent = message.parent or parent
                    message.save(update_fields=("parent", "updated_at"))
            if after_landing is not None:
                after_landing(message)
            if source_thread_changed and not historical and not message.is_trashed:
                message_ingested.send(sender=self.model, instance=message)
            return message, ()
        metadata = {**envelope_metadata, _SYNC_HASH_KEY: content_hash}
        parent = self._resolve_reply_parent(parsed, channel=channel, explicit_thread=explicit_thread)
        defaults = {
            "thread_id": prior.thread_id if expected_thread is not None else thread.pk if thread is not None else None,
            "channel_id": channel.pk if channel is not None else None,
            "sender_id": sender.pk if sender is not None else None,
            "parent_id": parent.pk if parent is not None else None,
            "platform": parsed.platform,
            "direction": parsed.direction,
            "status": self.model.MessageStatus.SYNCED,
            # Record chatter and public posts share the native COMMENT kind.
            "message_type": (
                self.model.MessageKind.COMMENT
                if thread is not None and (
                    thread.modality == thread_model.Modality.PUBLIC_THREAD or thread.is_record_attached()
                )
                else self.model.MessageKind.CHAT
                if parsed.thread is not None or explicit_thread is not None
                else self.model.MessageKind.EMAIL
            ),
            "preview": strip_null_bytes(_preview(parsed.body))[: self.model.preview.field.max_length],
            "sent_at": parsed.sent_at,
            "received_at": received_at,
            "metadata": metadata,
        }
        if created:
            for field, value in defaults.items():
                setattr(message, field, value)
            message.save()
        prior_hashes: list[str] = []
        retain_authored_parts = False
        if not created:
            message = authored_message or self.model._base_manager.select_for_update().get(pk=prior.pk)
            defaults["metadata"] = _bounded_message_metadata(
                self.model.ingest_metadata(metadata, previous=message.metadata),
            )
            if message.is_locally_written:
                # Provider echoes cannot change locally authored envelope identity.
                defaults["direction"] = message.direction
                defaults["sender_id"] = message.sender_id
                defaults["message_type"] = message.message_type
                defaults["status"] = message.status
                for field in ("thread_id", "parent_id", "platform", "preview", "sent_at"):
                    defaults[field] = getattr(message, field)
                thread = message.thread
                retain_authored_parts = True
            if explicit_thread is not None and message.thread_id != explicit_thread.pk and expected_thread is None:
                raise ValueError("Source message already belongs to a different explicit thread.")
            prior_hashes = self._content_fragment_hashes(part_model, message)
            for field, value in defaults.items():
                setattr(message, field, value)
            message.save()
        if not retain_authored_parts:
            message.parts.all().delete()
            position = self._write_envelope_parts(message, parsed, created_by_id=created_by_id)
            if parsed.body is not None:
                self._build_parts(message, parsed.body, parent=None, position=position, created_by_id=created_by_id)
            if not created:
                new_hashes = self._content_fragment_hashes(part_model, message)
                if new_hashes != prior_hashes:
                    # A provider edit: the row survives, the parts relinked — record what
                    # was replaced by hash (the old text lives on as shared fragments).
                    message.edit_history = [
                        _edit_history_entry(edited_by_id=None, prev_fragment_hashes=prior_hashes),
                        *(message.edit_history or []),
                    ]
                    message.save(update_fields=("edit_history", "updated_at"))
        handles = (
            () if retain_authored_parts else self._write_participants(message, thread, parsed, sender, created_by_id)
        )
        if expected_thread is not None:
            message = self.correct_source_thread(message, channel=channel, expected_thread=expected_thread,
                                                 thread=explicit_thread)
        # Reconcile the denormalised thread counters. The winning thread gains the
        # message whenever it is a fresh row or a re-sync re-resolved an existing message
        # onto a *different* thread (e.g. a References parent that only just landed). The
        # losing thread is recounted from its survivors — but only when there was one:
        # the prior thread may be NULL (a standalone provider event), in which case
        # the winner still gains the message and there is no
        # loser to recount. Gating the winner's bump on ``created`` alone dropped exactly
        # that NULL-prior re-home; an idempotent re-sync into the same thread is a no-op.
        thread_changed = expected_thread is None and prior is not None and prior.thread_id != message.thread_id
        if thread_changed and prior.thread_id is not None:
            losing_thread = thread_model._base_manager.select_for_update().get(pk=prior.thread_id)
            self._recount_thread(losing_thread)
        if after_landing is not None:
            after_landing(message)
        if (created or thread_changed) and not message.is_trashed and thread is not None:
            self.advance_thread(message)
        if (created or thread_changed or source_thread_changed) and not historical and not message.is_trashed:
            message_ingested.send(sender=self.model, instance=message)
        return message, handles

    def _resolve_reply_parent(self, parsed: ParsedMessage, *, channel: Any, explicit_thread: Any = None) -> Any:
        """Resolve ``in_reply_to`` onto the parent ``Message`` row, or ``None``.

        The single-parent reply pointer names the ingesting channel's physical
        parent, riding the non-unique ``MD5(external_id)`` index. Display identity
        never selects a different account's reply transport.
        A reply that lands before its parent keeps ``NULL``; the no-op re-sync path
        backfills the pointer once the parent exists, so a re-run of the same
        import heals ordering gaps without rebuilding unchanged rows.
        """

        reply_to = parsed.in_reply_to
        if not reply_to or reply_to == parsed.external_id:
            return None
        queryset = _external_id_annotated(self.model._base_manager).filter(
            _external_id_q(reply_to),
            platform=parsed.platform, channel=channel,
        )
        if explicit_thread is not None:
            queryset = queryset.filter(channel=channel, thread=explicit_thread)
        return queryset.order_by("pk").first()

    @staticmethod
    def _content_fragment_hashes(part_model: Any, message: Any) -> list[str]:
        """Return the message's content fragment hashes, envelope roles excluded.

        The edit-history decision keys on these: a changed retained-header list or
        title is envelope churn, not a provider edit of what was said.
        """

        return list(
            part_model._base_manager.filter(message=message, fragment__isnull=False)
            .exclude(role__in=(part_model.PartRole.TITLE, part_model.PartRole.HEADER))
            .order_by("position", "pk")
            .values_list("fragment__hash", flat=True)
        )

    def _write_envelope_parts(self, message: Any, parsed: ParsedMessage, *, created_by_id: Any) -> int:
        """Write the sparse ``TITLE``/``HEADER`` parts; return the next top-level position.

        Only messages that *have* a subject or retained headers pay for rows — an
        instant message writes nothing here. The values are content-addressed like
        any body text, so a subject repeated across a thread or a ``List-Id`` shared
        by ten thousand messages is one fragment row.
        """

        part_model = apps.get_model("messaging", "Part")
        fragment_model = apps.get_model("messaging", "Fragment")
        position = 0
        subject = strip_null_bytes(parsed.subject or "").strip()
        if subject:
            fragment = fragment_model.objects.upsert(text=subject, created_by_id=created_by_id)
            part_model.objects.create(
                message_id=message.pk,
                position=position,
                type="text/plain",
                role=part_model.PartRole.TITLE,
                fragment_id=fragment.pk if fragment is not None else None,
                created_by_id=created_by_id,
            )
            position += 1
        for name, value in parsed.headers:
            name = strip_null_bytes(name or "").strip().lower()
            value = strip_null_bytes(value or "").strip()
            if not name or not value:
                continue
            fragment = fragment_model.objects.upsert(
                text=value, kind=fragment_model.FragmentKind.HEADER, created_by_id=created_by_id
            )
            part_model.objects.create(
                message_id=message.pk,
                position=position,
                type="text/plain",
                role=part_model.PartRole.HEADER,
                name=name,
                fragment_id=fragment.pk if fragment is not None else None,
                created_by_id=created_by_id,
            )
            position += 1
        return position

    def _build_parts(
        self,
        message: Any,
        parsed: ParsedPart,
        *,
        parent: Any,
        position: int,
        created_by_id: Any,
        nameless: list[int] | None = None,
    ) -> None:
        part_model = apps.get_model("messaging", "Part")
        fragment_model = apps.get_model("messaging", "Fragment")
        if nameless is None:
            # Per-message running count of byte parts that arrived without a name, so a
            # message with several unnamed attachments derives distinct ``-{index}``
            # names. One counter threads through the whole recursive body tree.
            nameless = [0]
        file_ref = None
        fragment = None
        part_name = parsed.name
        if parsed.content is not None:
            if not part_name:
                # The source gave this attachment no name: derive one from the owning
                # message's kind/external-id and this part's mime/Content-ID, so every
                # backend shares one rule and the stored File gets a meaningful name.
                part_name = derived_part_name(
                    mime=parsed.type,
                    cid=parsed.cid,
                    external_id=message.external_id,
                    is_email=message.message_type == self.model.MessageKind.EMAIL,
                    index=nameless[0],
                )
                nameless[0] += 1
            file_ref = self._ingest_file(parsed, created_by_id, filename=part_name)
        elif parsed.text and not parsed.children:
            part_role = part_model.PartRole
            fragment_kind = fragment_model.FragmentKind
            fragment = fragment_model.objects.upsert(
                text=parsed.text,
                kind=(
                    fragment_kind.SIGNATURE
                    if parsed.role == part_role.SIGNATURE
                    else fragment_kind.QUOTE
                    if parsed.role == part_role.QUOTED
                    else fragment_kind.PARAGRAPH
                ),
                created_by_id=created_by_id,
            )
        part_disposition = part_model._meta.get_field("disposition").to_python(parsed.disposition)
        part_role_value = part_model._meta.get_field("role").to_python(parsed.role)
        assert isinstance(part_disposition, part_model.Disposition)
        assert isinstance(part_role_value, part_model.PartRole)
        part = part_model.objects.create(
            message_id=message.pk,
            parent_id=parent.pk if parent is not None else None,
            position=position,
            type=parsed.type,
            disposition=part_disposition,
            role=part_role_value,
            cid=parsed.cid,
            name=part_name,
            fragment_id=fragment.pk if fragment is not None else None,
            file_id=file_ref.pk if file_ref is not None else None,
            created_by_id=created_by_id,
        )
        for index, child in enumerate(parsed.children):
            self._build_parts(
                message,
                child,
                parent=part,
                position=index,
                created_by_id=created_by_id,
                nameless=nameless,
            )

    def _ingest_file(self, parsed: ParsedPart, created_by_id: Any, *, filename: str) -> Any:
        """Persist attachment bytes through the storage File owner; returns the File or None.

        Delegates to ``File.objects.ingest_bytes`` — the storage owner's
        server-side byte intake (draft → write → finalize) — so the attachment
        lands content-addressed and ``Part.file`` resolves. Its ``owner`` grants
        the channel owner access; ``created_by`` remains actor attribution.
        ``filename`` is the resolved part name (the caller derives one when the
        source gave none); on a content-addressed dedup hit the existing File keeps
        its first name, so the reliable per-message name lives on ``Part.name``.
        """

        if parsed.content is None:
            return None
        file_model = apps.get_model("storage", "File")
        return file_model.objects.ingest_bytes(
            parsed.content,
            filename=filename or fallback_attachment_name(parsed.type),
            owner_id=created_by_id,
        )

    def _write_participants(
        self,
        message: Any,
        thread: Any,
        parsed: ParsedMessage,
        sender: Any,
        created_by_id: Any,
    ) -> list[Any]:
        participant_model = apps.get_model("messaging", "Participant")
        handle_model = apps.get_model("parties", "Handle")
        participant_model.objects.filter(message=message).delete()
        seen: set[tuple[Any, str]] = set()
        handles: list[Any] = []
        if sender is not None:
            seen.add((sender.pk, str(participant_model.ParticipantRole.FROM)))
            participant_model.objects.create(
                message_id=message.pk,
                thread_id=thread.pk,
                handle_id=sender.pk,
                role=participant_model.ParticipantRole.FROM,
                created_by_id=created_by_id,
            )
            handles.append(sender)
        for recipient in parsed.recipients:
            handle = handle_model.objects.upsert(
                platform=recipient.handle.platform,
                value=recipient.handle.value,
                created_by_id=created_by_id,
                display_name=recipient.handle.display_name,
                external_id=recipient.handle.external_id,
                metadata=recipient.handle.metadata,
            )
            # The write path owns envelope dedup (the unique constraint is the
            # backstop): any producer may repeat an address within one role.
            key = (handle.pk, str(recipient.role))
            if key in seen:
                continue
            seen.add(key)
            recipient_role = participant_model._meta.get_field("role").to_python(recipient.role)
            assert isinstance(recipient_role, participant_model.ParticipantRole)
            participant_model.objects.create(
                message_id=message.pk,
                thread_id=thread.pk,
                handle_id=handle.pk,
                role=recipient_role,
                created_by_id=created_by_id,
            )
            handles.append(handle)
        return handles
