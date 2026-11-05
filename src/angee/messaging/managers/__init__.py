"""Canonical Messaging managers and historical public imports."""
from angee.messaging.managers.channels import ChannelManager as ChannelManager
from angee.messaging.managers.content import FragmentManager as FragmentManager
from angee.messaging.managers.content import MessageEdgeManager as MessageEdgeManager
from angee.messaging.managers.content import PartManager as PartManager
from angee.messaging.managers.content import _cid_slug as _cid_slug
from angee.messaging.managers.content import derived_part_name as derived_part_name
from angee.messaging.managers.messages import DeliveryReconciliationBusy as DeliveryReconciliationBusy
from angee.messaging.managers.messages import MessageManager as MessageManager
from angee.messaging.managers.messages import MessageStarManager as MessageStarManager
from angee.messaging.managers.messages import MessageSubtypeManager as MessageSubtypeManager
from angee.messaging.managers.messages import ReactionManager as ReactionManager
from angee.messaging.managers.messages import _preview as _preview
from angee.messaging.managers.messages import logger as logger
from angee.messaging.managers.messages import message_subtype_options as message_subtype_options
from angee.messaging.managers.notifications import ThreadFollowerManager as ThreadFollowerManager
from angee.messaging.managers.notifications import ThreadNotificationManager as ThreadNotificationManager
from angee.messaging.managers.threads import ThreadAttachmentManager as ThreadAttachmentManager
from angee.messaging.managers.threads import ThreadManager as ThreadManager
from angee.messaging.managers.threads import (
    _assert_canonical_composes_thread_mixin as _assert_canonical_composes_thread_mixin,
)
from angee.messaging.managers.threads import normalize_subject as normalize_subject
from angee.messaging.querysets import _MESSAGE_ORDER as _MESSAGE_ORDER
from angee.messaging.querysets import _SIGNING_TEXT_BATCH as _SIGNING_TEXT_BATCH
from angee.messaging.querysets import _WS_RE as _WS_RE
from angee.messaging.querysets import AUTOMATED_MAIL_HEADERS as AUTOMATED_MAIL_HEADERS
from angee.messaging.querysets import ChannelQuerySet as ChannelQuerySet
from angee.messaging.querysets import MessageQuerySet as MessageQuerySet
from angee.messaging.querysets import PartQuerySet as PartQuerySet
from angee.messaging.querysets import ThreadFollowerQuerySet as ThreadFollowerQuerySet
from angee.messaging.querysets import ThreadNotificationQuerySet as ThreadNotificationQuerySet
from angee.messaging.querysets import ThreadQuerySet as ThreadQuerySet
from angee.messaging.querysets import _external_id_annotated as _external_id_annotated
from angee.messaging.querysets import _message_search_query as _message_search_query
from angee.messaging.querysets import _MessageChronology as _MessageChronology
from angee.messaging.querysets import _resolve_user_id as _resolve_user_id
