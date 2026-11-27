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
