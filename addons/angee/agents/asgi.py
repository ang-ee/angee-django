"""ACP WebSocket contribution for in-process agents."""

from django.urls import re_path

from angee.agents.consumers import AgentACPConsumer
from angee.agents.protocol import ACP_PATH

websocket_urlpatterns = [
    re_path(rf"^{ACP_PATH.lstrip('/')}(?P<agent>[^/]+)/$", AgentACPConsumer.as_asgi(), name="agent-acp"),
]
