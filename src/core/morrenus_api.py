"""Deprecated compatibility shim.

This module used to hold both the Hubcap HTTP client and the app-wide
orchestration helpers. That code now lives in
:mod:`core.providers.hubcap_impl` (Hubcap transport) and
:mod:`core.manifest_provider` (provider routing facade).

Existing ``from core import morrenus_api`` imports keep working unchanged. New
code should import :mod:`core.manifest_provider` instead.
"""

from core.manifest_provider import *  # noqa: F401,F403
from core.manifest_provider import __all__  # noqa: F401
from core.providers.hubcap_impl import (  # noqa: F401
    BASE_URL,
    _get_headers,
    _handle_request_exception,
    compute_api_key_expiry_info,
    get_session,
    is_stats_cache_from_previous_utc_day,
)
