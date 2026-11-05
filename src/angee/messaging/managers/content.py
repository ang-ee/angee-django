"""Messaging content factories and write owners."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db import NotSupportedError, connections, models, router, transaction

from angee.base.serialization import strip_null_bytes
from angee.iam.managers import ProtectedManager
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
from angee.storage.uploads import attachment_extension, fallback_attachment_name, truncate_filename

if TYPE_CHECKING:

    from angee.messaging.models import (
        Fragment,
        Message,
        MessageEdge,
        Part,
    )




# A fragment quoted by more than this many messages is boilerplate (a disclaimer or
# repeated signature); quote-linking it would join the whole corpus, so skip it.
_BOILERPLATE_CUTOFF = 100


# One rule for naming a message part that arrived without its own filename. It lives
# here beside the ingest write path — its only live caller — because the facts that
# select the name stem are messaging's: a chat message's id (carried in the message
# ``external_id`` a chat backend composes as ``<chat>/<id>``) and an email part's
# Content-ID. Storage's ``uploads`` module is ORM-free and messaging-agnostic, so the
# rule cannot live there; it composes storage's ``attachment_extension`` for the
# extension and ``fallback_attachment_name`` for the shared floor instead, keeping the
# byte→extension map owned once by storage. The backfill migration freezes its own copy.
_CID_SLUG_DISALLOWED_RE = re.compile(r"[^A-Za-z0-9._-]")


_CID_SLUG_MAX = 80


def _cid_slug(cid: str) -> str:
    """Slug an email Content-ID into a filename stem.

    Strips the ``<>`` delimiters, keeps only ``[A-Za-z0-9._-]`` (dropping the rest,
    so a ``local@domain`` cid concatenates), and caps the length so a long cid never
    blows past the ``Part.name``/``File.filename`` column width.
    """

    core = (cid or "").strip().strip("<>")
    return _CID_SLUG_DISALLOWED_RE.sub("", core)[:_CID_SLUG_MAX]


def derived_part_name(*, mime: str, cid: str, external_id: str, is_email: bool, index: int = 0) -> str:
    """Return the display filename for a message part that arrived without its own name.

    One rule, applied at ingest for every backend and frozen into the backfill:

    - an **email** part (``is_email`` — the message's ``EMAIL`` kind) carrying a
      ``Content-ID`` (an inline image — the near-universal nameless email part)
      takes ``inline-{cid-slug}{ext}``;
    - any **other** message part takes the vendor message id — the segment after
      the last ``/`` of the ``<chat>/<id>`` ``external_id`` every chat backend
      composes — as ``{id}{ext}``, with a ``-{index}`` suffix distinguishing the
      second and later nameless parts of one message. This covers every chat
      kind, not just ``CHAT``: a Telegram broadcast post lands as a ``COMMENT``
      yet carries the same id-shaped ``external_id``;
    - anything left keeps storage's shared ``attachment{ext}`` fallback.

    ``is_email`` is the discriminator because ``Message.message_type`` is the
    framework's own classification — derived at ingest from the thread shape and
    denormalised onto every row — so the identical fact drives the live rule and
    the historical backfill, while ``external_id``'s ``/`` shape only *carries*
    the id the non-email branch reads. The extension is storage's
    ``attachment_extension``.
    """

    extension = attachment_extension(mime)
    if is_email:
        slug = _cid_slug(cid)
        if slug:
            return f"inline-{slug}{extension}"
    else:
        message_id = (external_id or "").rsplit("/", 1)[-1]
        if message_id:
            suffix = f"-{index}" if index else ""
            return f"{message_id}{suffix}{extension}"
    return fallback_attachment_name(mime)


# The text-search configuration for fragment vectors: mail is multilingual, so the
# non-stemming config keeps one language's stemmer from skewing every other.
_SEARCH_CONFIG = "simple"


# Postgres's to_tsvector rejects input over 1 MiB; vectorise at most this many
# UTF-8 bytes of a fragment (half the limit — headroom for the vector itself).
# The regression fixture was a 1.6 MB text part that failed every sync retry.
_SEARCH_MAX_BYTES = 512 * 1024


class FragmentManager[FragmentT: Fragment](ProtectedManager[FragmentT]):
    """Content-addressed text store: one row per distinct (null-stripped) text."""

    def search_query(self, term: str, *, search_type: str = "plain") -> SearchQuery:
        """Build a query with the same configuration as this store's vectors."""

        return SearchQuery(strip_null_bytes(term or "").strip(), search_type=search_type, config=_SEARCH_CONFIG)

    def upsert(self, *, text: str, kind: str = "paragraph", created_by_id: Any = None) -> FragmentT:
        """Get-or-create a fragment by the SHA-256 of its cleaned (null-stripped, trimmed) text.

        A new fragment's ``search`` vector is stamped in the same transaction — the
        row is immutable and dedup means each unique text is vectorised exactly
        once, so no trigger or async queue is needed however many messages share it.
        """

        text = strip_null_bytes(text).strip()
        encoded = text.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        with transaction.atomic(using=self.db):
            fragment, created = self.get_or_create(
                hash=digest,
                defaults={"text": text, "kind": kind, "created_by_id": created_by_id},
            )
            assert fragment._state.db is not None
            if created and connections[fragment._state.db].vendor == "postgresql":
                # tsvector is a Postgres type; on other vendors (the SQLite test
                # backend) the column stays NULL and full-text search is unavailable.
                # Postgres rejects to_tsvector input over 1MiB ("string is too long
                # for tsvector") and a text that size is a pathological payload, not
                # prose — vectorise a bounded prefix; the row keeps the full text.
                searchable: Any = "text"
                if len(encoded) > _SEARCH_MAX_BYTES:
                    searchable = models.Value(encoded[:_SEARCH_MAX_BYTES].decode("utf-8", errors="ignore"))
                self.model._base_manager.using(fragment._state.db).filter(pk=fragment.pk).update(
                    search=SearchVector(searchable, config=_SEARCH_CONFIG)
                )
        return fragment


_PartManagerBase = ProtectedManager.from_queryset(PartQuerySet)


class PartManager[PartT: Part](_PartManagerBase[PartT]):
    """Owns bounded part creation and message-scoped navigation over body-part rows.

    Tree reads falling back to the base manager require callers to authorize
    the parent Message, including record-scoped chatter projections. Each
    operation loads that message's parts once, with fragment/file/MIME joins,
    or reuses a native ``parts`` prefetch of already authorized rows with its
    parent/position/public-ID navigation columns loaded. The caller owns selected
    content and relation hints; navigation never forces unrequested body or file
    fields. Missing navigation columns fall back to the single joined read.
    No separate tree cache is kept.
    """

    def attach_file(
        self,
        message: Message,
        file: Any,
        *,
        position: int | None = None,
        name: str | None = None,
        created_by_id: Any = None,
    ) -> PartT:
        """Idempotently append a file under its locked parent on the selected database.

        The caller owns admission and source membership. Explicit name/position
        preserve a protocol's envelope; absent MIME metadata is octet-stream.
        """
        using = self._db or router.db_for_write(self.model, instance=message)
        with transaction.atomic(using=using):
            type(message).system_queryset(lock=("self",)).using(using).get(pk=message.pk)
            rows = self.db_manager(using)
            existing = self.model._base_manager.using(using).filter(message=message, file=file).first()
            if existing is not None:
                return existing
            if position is None:
                last = (
                    self.model._base_manager.using(using)
                    .filter(message=message)
                    .aggregate(
                        value=models.Max("position"),
                    )["value"]
                )
                position = 1 if last is None else last + 1
            file = type(file)._base_manager.using(using).select_related("mime_type").get(pk=file.pk)
            mime = file.mime_type
            return rows.create(
                message=message,
                file=file,
                position=position,
                type=mime.mime_type if mime is not None else "application/octet-stream",
                name=(file.filename or file.title or "attachment") if name is None else name,
                disposition=self.model.Disposition.ATTACHMENT,
                role=self.model.PartRole.BODY,
                created_by_id=message.created_by_id if created_by_id is None else created_by_id,
            )


    BODY_SEPARATOR = "\n\n"

    def plain_body_text(self, message: Message) -> str:
        """Join plain BODY fragments in the same depth-first order as the tree reader."""

        return self.BODY_SEPARATOR.join(
            fragment.text for part in self.reading_order_for_message(message)
            if part.role == self.model.PartRole.BODY and part.type == "text/plain"
            and (fragment := part.fragment) is not None
        )

    def plain_body_sql(self, message_id_sql: str, *, connection: Any) -> tuple[str, list[Any]]:
        """Project the tree reader's plain body in SQL for a trusted message-id expression.

        Sibling ranks order each depth-first path by (position, pk). Only rooted
        parts of this message participate, matching reading_order_for_message's
        treatment of foreign parents and unrooted cycles. SQL consumers supply
        identifiers/expressions, never user text; the separator is a bound value.
        The aggregate's own ORDER BY fixes the join order on every backend,
        which needs Django's aggregate ORDER BY feature (SQLite 3.44+).
        """

        if not connection.features.supports_aggregate_order_by_clause:
            raise NotSupportedError("The plain-body projection needs ordered string aggregation.")
        quote = connection.ops.quote_name
        parts = quote(self.model._meta.db_table)
        fragment_model = self.model.fragment.field.related_model
        assert fragment_model is not None
        fragments = quote(fragment_model._meta.db_table)
        rank = "printf('%%020d', sibling_rank)" if connection.vendor == "sqlite" else (
            "LPAD(CAST(sibling_rank AS text), 20, '0')"
        )
        aggregate = (
            f"SELECT STRING_AGG(f.text, %s ORDER BY t.path) FROM tree t "
            f"JOIN {fragments} f ON f.id = t.fragment_id WHERE t.role = 'body' AND t.type = 'text/plain'"
        )
        return f"""(WITH RECURSIVE siblings AS (
            SELECT p.*, ROW_NUMBER() OVER (PARTITION BY parent_id ORDER BY position, id) AS sibling_rank
            FROM {parts} p WHERE message_id = {message_id_sql}
        ), tree AS (
            SELECT id, fragment_id, role, type, {rank} AS path FROM siblings WHERE parent_id IS NULL
            UNION ALL
            SELECT s.id, s.fragment_id, s.role, s.type, t.path || '/' || {rank}
            FROM siblings s JOIN tree t ON s.parent_id = t.id
        ) SELECT COALESCE(({aggregate}), ''))""", [self.BODY_SEPARATOR]

    def create(self, **fields: Any) -> PartT:
        """Bound source text and discard malformed Content-IDs before writing.

        A truncated Content-ID would reference a different part. Keep the part
        but clear that identifier when it cannot fit its declared column.
        """

        if "name" in fields:
            max_name_length = self.model.name.field.max_length
            assert max_name_length is not None
            fields["name"] = truncate_filename(
                strip_null_bytes(fields["name"] or ""),
                max_name_length,
            )
        if "type" in fields:
            fields["type"] = strip_null_bytes(fields["type"] or "")[: self.model.type.field.max_length]
        if "cid" in fields:
            cid = strip_null_bytes(fields["cid"] or "")
            max_cid_length = self.model.cid.field.max_length
            assert max_cid_length is not None
            fields["cid"] = cid if len(cid) <= max_cid_length else ""
        return super().create(**fields)

    def reading_order_for_message(self, message: Message) -> list[Part]:
        """Return ``message`` parts flattened in depth-first reading order.

        Roots and siblings sort by ``(position, str(sqid))``. Unreachable
        orphan/cycle components follow in ``(parent_id, position, str(sqid))``
        order, visiting each row once. See the manager's read/cache contract.
        """

        by_id, siblings = self._parts_for_message(message)
        roots = sorted(
            by_id.values(),
            key=lambda part: (
                -1 if part.parent_id is None else part.parent_id,
                part.position,
                str(part.sqid),
            ),
        )
        return self._walk_parts(roots, siblings, seen=set())

    def children_for_message(self, message: Message, parent_id: Any | None) -> list[Part]:
        """Return direct children in sibling order; ``parent_id=None`` selects roots.

        IDs are Part primary keys within ``message``; an unknown or foreign
        parent returns an empty list. See the manager's read/cache contract.
        """

        by_id, siblings = self._parts_for_message(message)
        if parent_id is not None and parent_id not in by_id:
            return []
        return siblings.get(parent_id, [])

    def ancestors_for_message(
        self,
        message: Message,
        part_id: Any,
        *,
        stop_at: Callable[[Part], bool] | None = None,
    ) -> list[Part]:
        """Return strict ancestors of a Part primary key, nearest parent first.

        Stay within ``message``; unknown IDs return an empty list and missing
        parents or cycles end the walk without returning the starting part.
        A row matching ``stop_at`` is included, but its ancestors are skipped.
        Callers apply kind, content, or membership predicates to the returned
        rows. See the manager's read/cache contract.
        """

        by_id, _siblings = self._parts_for_message(message)
        part = by_id.get(part_id)
        ancestors: list[Part] = []
        seen = {part_id}
        while part is not None:
            part = by_id.get(part.parent_id)
            if part is None or part.pk in seen:
                break
            seen.add(part.pk)
            ancestors.append(part)
            if stop_at is not None and stop_at(part):
                break
        return ancestors

    def descendants_for_message(
        self,
        message: Message,
        part_id: Any,
        *,
        stop_at: Callable[[Part], bool] | None = None,
    ) -> list[Part]:
        """Return strict descendants of a Part primary key in depth-first reading order.

        Stay within ``message``; unknown IDs return an empty list. Visit each
        row once and exclude the starting part, including in cycles. A row
        matching ``stop_at`` is included, but its descendants are skipped;
        the starting part is not tested. Callers apply content/existence
        predicates to these rows. See the manager's read/cache contract.
        """

        by_id, siblings = self._parts_for_message(message)
        if part_id not in by_id:
            return []
        return self._walk_parts(siblings.get(part_id, []), siblings, seen={part_id}, stop_at=stop_at)

    def _parts_for_message(self, message: Message) -> tuple[dict[Any, Part], dict[Any | None, list[Part]]]:
        """Index one message read by primary key and ordered siblings.

        Foreign parents remain absent from the index, so navigation never
        follows a forward FK descriptor onto another message.
        """

        parts: list[Part] | None = None
        cache = getattr(message, "_prefetched_objects_cache", None)
        if cache is not None and "parts" in cache:
            # Native prefetch already materialized these rows under the caller's
            # authorization. Re-evaluating its queryset would re-enter REBAC
            # before this owner can reject incomplete rows.
            parts = cache["parts"]._result_cache
            if any(
                {"parent_id", "position", "sqid"} & part.get_deferred_fields()
                for part in (parts if parts is not None else ())
            ):
                parts = None
        if parts is None:
            parts = list(
                self.model._base_manager.filter(message=message)
                .select_related("fragment", "file", "file__mime_type")
                .order_by("parent_id", "position", "sqid")
            )
        by_id: dict[Any, Part] = {}
        siblings: dict[Any | None, list[Part]] = {}
        for part in parts:
            by_id[part.pk] = part
            siblings.setdefault(part.parent_id, []).append(part)
        for rows in siblings.values():
            rows.sort(key=lambda part: (part.position, str(part.sqid)))
        return by_id, siblings

    @staticmethod
    def _walk_parts(
        roots: Sequence[Part],
        siblings: dict[Any | None, list[Part]],
        *,
        seen: set[Any],
        stop_at: Callable[[Part], bool] | None = None,
    ) -> list[Part]:
        """Flatten ordered branches once, pruning below caller-selected boundaries."""

        ordered: list[Part] = []
        for root in roots:
            pending = [root]
            while pending:
                part = pending.pop()
                if part.pk in seen:
                    continue
                seen.add(part.pk)
                ordered.append(part)
                if stop_at is None or not stop_at(part):
                    pending.extend(reversed(siblings.get(part.pk, [])))
        return ordered


class MessageEdgeManager[MessageEdgeT: MessageEdge](ProtectedManager[MessageEdgeT]):
    """Owns the cross-message graph — derived quote edges from shared fragments."""

    def _edge_fields(self, *, created_by_id: Any, fragment: Any = None, confidence: float = 1.0) -> dict[str, Any]:
        """Return the non-key columns of one edge row — the write shape shared by
        :meth:`relate` and the batched quotation builder, so the edge field set and the
        ``created_by`` default live once here with the table owner rather than in each
        caller. ``fragment`` accepts a row or its id.
        """

        return {
            "fragment_id": getattr(fragment, "pk", fragment),
            "confidence": confidence,
            "created_by_id": created_by_id,
        }

    def for_message(self, message: Any) -> list[Any]:
        """Return ``message``'s edges (both directions) whose far endpoint the actor may read.

        Edge rows read through the actor-scoped manager, and an edge is kept only
        when its far endpoint is itself readable: a quote edge links across
        channels by construction (fragments content-address globally), so an
        unscoped read would hand out another account's message content.
        """

        message_model = apps.get_model("messaging", "Message")
        edges = list(
            self.filter(models.Q(src_id=message.pk) | models.Q(dst_id=message.pk))
            .select_related("fragment", "src", "dst")
            .order_by("pk")
        )
        other_ids = {edge.src_id for edge in edges} | {edge.dst_id for edge in edges}
        other_ids.discard(message.pk)
        readable: set[Any] = (
            set(message_model.objects.filter(pk__in=other_ids).values_list("pk", flat=True)) if other_ids else set()
        )
        readable.add(message.pk)
        return [edge for edge in edges if edge.src_id in readable and edge.dst_id in readable]

    def relate(
        self,
        src: Any,
        dst: Any,
        *,
        kind: Any,
        created_by_id: Any,
        fragment: Any = None,
        confidence: float = 1.0,
    ) -> Any:
        """Write one typed edge from ``src`` to ``dst``, idempotent on the (src, dst, kind) key.

        The single edge-write entry point on the table owner: a posts producer relating
        two messages (mention/crosspost/forward) writes through this one shape instead of
        its own ``get_or_create``, and the batched quotation builder lands the same
        :meth:`_edge_fields` columns. ``src``/``dst``/``fragment`` accept a row or its id;
        returns the edge, creating it only when the (src, dst, kind) triple is new.
        """

        edge, _created = self.get_or_create(
            src_id=getattr(src, "pk", src),
            dst_id=getattr(dst, "pk", dst),
            kind=kind,
            defaults=self._edge_fields(created_by_id=created_by_id, fragment=fragment, confidence=confidence),
        )
        return edge

    def create_for_message(self, message: Any) -> int:
        """Write quote edges from ``message`` to others sharing a non-boilerplate fragment.

        Skips fragments quoted by more than :data:`_BOILERPLATE_CUTOFF` messages;
        edge direction runs from the earlier message to the later one.
        """

        part_model = apps.get_model("messaging", "Part")
        part_role = part_model.PartRole
        # Titles and headers are envelope facts, not quoted prose: a "Re: X" title
        # fragment shared by a whole thread must not mint quote edges.
        fragment_ids = list(
            part_model.objects.filter(message=message, fragment__isnull=False)
            .exclude(role__in=(part_role.QUOTED, part_role.SIGNATURE, part_role.TITLE, part_role.HEADER))
            .values_list("fragment_id", flat=True)
            .distinct()
        )
        if not fragment_ids:
            return 0
        # One pass over every sharing part: pull each sharer's message id *and*
        # sent_at together, so edge direction needs no per-pair lookup. Excluding
        # this message's own parts leaves only the others to link.
        sharers_by_fragment: dict[Any, dict[Any, Any]] = {}
        for fragment_id, other_id, other_sent_at in (
            part_model.objects.filter(fragment_id__in=fragment_ids)
            .exclude(message_id=message.pk)
            # Envelope roles never witness quoting on the sharer side either: a
            # body paragraph equal to another message's *subject* is not a quote.
            .exclude(role__in=(part_role.TITLE, part_role.HEADER))
            .values_list("fragment_id", "message_id", "message__sent_at")
        ):
            sharers_by_fragment.setdefault(fragment_id, {})[other_id] = other_sent_at
        # Collapse to one directed edge per message pair — the first fragment that
        # links a pair wins, matching the get_or_create this replaces. Boilerplate (a
        # disclaimer/signature quoted by the whole corpus) is skipped past the cutoff
        # so linking it does not join everything.
        fragment_by_pair: dict[tuple[Any, Any], Any] = {}
        for fragment_id, others in sharers_by_fragment.items():
            if len(others) > _BOILERPLATE_CUTOFF:
                continue
            for other_id, other_sent_at in others.items():
                pair = self._direction(message.pk, message.sent_at, other_id, other_sent_at)
                fragment_by_pair.setdefault(pair, fragment_id)
        if not fragment_by_pair:
            return 0
        kind = self.model.EdgeKind.QUOTE
        # One existence read replaces the per-pair get_or_create: every candidate edge
        # touches this message, so read the quote edges on it once, then bulk-insert the
        # missing pairs under one transaction (``ignore_conflicts`` covers a concurrent
        # run, so a re-ingest that re-derives the same edges stays a no-op).
        with transaction.atomic():
            existing_pairs = set(
                self.filter(kind=kind)
                .filter(models.Q(src_id=message.pk) | models.Q(dst_id=message.pk))
                .values_list("src_id", "dst_id")
            )
            new_edges = [
                self.model(
                    src_id=src_id,
                    dst_id=dst_id,
                    kind=kind,
                    **self._edge_fields(created_by_id=message.created_by_id, fragment=fragment_id),
                )
                for (src_id, dst_id), fragment_id in fragment_by_pair.items()
                if (src_id, dst_id) not in existing_pairs
            ]
            self.bulk_create(new_edges, ignore_conflicts=True)
        return len(new_edges)

    def _direction(self, a_id: Any, a_sent_at: Any, b_id: Any, b_sent_at: Any) -> tuple[Any, Any]:
        """Order an edge from the earlier message to the later one (by sent_at, then pk).

        Both sent_at values are already in hand from the single sharers query, so
        direction is a pure comparison — no extra lookup.
        """

        if a_sent_at and b_sent_at and a_sent_at != b_sent_at:
            return (a_id, b_id) if a_sent_at < b_sent_at else (b_id, a_id)
        return (a_id, b_id) if a_id < b_id else (b_id, a_id)
