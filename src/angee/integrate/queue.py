"""Narrow compatibility alias for angee-odoo; remove when its companion moves to ``Bridge.request_sync``."""

from angee.integrate.models import Bridge

queue_bridge_sync = Bridge.request_sync
