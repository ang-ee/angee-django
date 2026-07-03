"""Compatibility names for bridge sync ingestion markers."""

from __future__ import annotations

from angee.base.sync import sync_ingestion_active, sync_ingestion_context

bridge_sync_context = sync_ingestion_context
bridge_sync_active = sync_ingestion_active

__all__ = ["bridge_sync_active", "bridge_sync_context"]
