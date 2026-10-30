"""Connection attribution through the host ASGI contribution seam."""

from angee.provenance.middleware import ChangesetScopeMiddleware

websocket_middleware = [ChangesetScopeMiddleware]
