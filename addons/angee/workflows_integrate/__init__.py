"""Composition addon connecting archive extraction to workflow decisions.

The addon owns the vendor-free extractor registry and the probe, mapping-gate,
and execute workflow steps. Vendor addons contribute concrete extractors through
settings and retain ownership of archive parsing and idempotent domain ingest.
"""
