"""Optional Channels authentication contribution consumed by GraphQL."""
from channels.auth import AuthMiddlewareStack
websocket_middleware = [AuthMiddlewareStack]
