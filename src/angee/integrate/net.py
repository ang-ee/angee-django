"""Historical imports of canonical outbound URL validators."""

from angee.integrate.validators import (
    ALLOWED_HTTP_SCHEMES,
    METADATA_IPS,
    AddressNotAllowedError,
    AddressResolutionError,
    canonical_address,
    is_unsafe_address,
    parse_http_url,
    resolved_addresses,
    validate_public_url,
)

__all__ = [
    "ALLOWED_HTTP_SCHEMES",
    "AddressNotAllowedError",
    "AddressResolutionError",
    "METADATA_IPS",
    "canonical_address",
    "is_unsafe_address",
    "parse_http_url",
    "resolved_addresses",
    "validate_public_url",
]
