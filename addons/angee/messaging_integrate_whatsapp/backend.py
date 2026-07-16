"""WhatsApp channel backend: live-session identity and pairing projection."""

from __future__ import annotations

from angee.integrate.live import PairingState, SessionLoggedOut
from angee.messaging.backends import LiveChannelBackend, ParsedMessage
from angee.messaging_integrate_whatsapp.client import DuplicateAccountRejected, WhatsappPairingType
from angee.messaging_integrate_whatsapp.constants import SESSION_QUEUE
from angee.messaging_integrate_whatsapp.parser import (
    ChatMessage,
    MediaItem,
    bare_jid,
    parsed_message,
    phone_for_jid,
)


class WhatsAppChannelBackend(LiveChannelBackend):
    """Channel backend for a linked WhatsApp account — one live session per channel."""

    key = "whatsapp"
    label = "WhatsApp"
    icon = "message-circle"
    session_queue = SESSION_QUEUE
    session_class = "angee.messaging_integrate_whatsapp.session.WhatsAppSession"
    state_identity_key = "own_jid"
    media_item_class = MediaItem

    def normalize_account_id(self, raw: str) -> str:
        """Return WhatsApp's durable bare-JID account identity."""

        return bare_jid(raw)

    def account_label(self, own_id: str) -> str:
        """Return a human label for a WhatsApp account id."""

        return phone_for_jid(own_id) or own_id

    def pairing_report_identity(self, own_id: str) -> dict[str, str]:
        """Return the WhatsApp-shaped identity fields stored in pairing reports."""

        report = {"jid": own_id}
        if phone := phone_for_jid(own_id):
            report["phone"] = phone
        return report

    def parse_live_message(self, message: ChatMessage) -> ParsedMessage:
        """Map one queued WhatsApp live message onto the neutral messaging seam."""

        return parsed_message(message)

    def duplicate_account_error(self) -> Exception:
        """Return the runtime error recorded for a duplicate WhatsApp account."""

        return DuplicateAccountRejected("Another channel already owns this WhatsApp account.")

    def logged_out_error(self) -> SessionLoggedOut:
        """Restore WhatsApp's operator-visible phone/device logout wording."""

        return SessionLoggedOut("The linked phone removed this device.")

    def pairing(self) -> WhatsappPairingType:
        """Project durable identity plus the latest transient report for the dialog.

        The row answers everything settled - ``PAUSED``/``STOPPED`` from the
        lifecycle, ``PAIRED`` from the claimed account identity - and the
        session's report fills in what is genuinely in flight. The wire shape is
        WhatsApp-specific: ``jid``/``phone`` and the ``whatsapp_pairing`` verbs.
        """

        report = self._pairing_report()
        reported = PairingState.from_report(report.get("state"))
        jid = str(self.bridge.subscription_state.get("own_jid") or report.get("jid") or "")
        state = self._pairing_state(reported=reported, identity=jid)
        duplicate = self._duplicate_owner(jid) if state is PairingState.DUPLICATE_ACCOUNT else None
        return WhatsappPairingType(
            state=state,
            qr=str(report.get("qr") or "") if state is PairingState.AWAITING_SCAN else "",
            jid=jid,
            phone=phone_for_jid(jid),
            duplicate_channel_id="" if duplicate is None else str(duplicate.sqid),
            duplicate_channel_name="" if duplicate is None else str(duplicate.display_name),
        )
