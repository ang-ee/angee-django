"""The channel a web form's responses are posted on."""

from __future__ import annotations

from angee.messaging.backends import ChannelBackend


class WebformChannelBackend(ChannelBackend):
    """A vendor-free channel whose messages are web form responses.

    It pulls nothing and delivers nothing. Its one source is the ``message``
    target of a form that names the channel
    (:class:`~angee.webforms_messaging.targets.MessageTarget`).
    """

    key = "webform"
    label = "Public Webform"
    quote_edges = False
