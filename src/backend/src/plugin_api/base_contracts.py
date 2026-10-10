"""Stable public Plugin API contract exports and compatibility decisions.

Core and frontend models have separate owners; consumers continue importing
this facade so the versioned API and schema references remain unchanged.
"""

from __future__ import annotations

from .base_contracts import *  # noqa: F403
