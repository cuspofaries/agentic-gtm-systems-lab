"""CRM abstraction + SQLite-backed mock implementation."""

from .base import CRMAdapter, CRMError
from .hubspot import HubSpotCRM
from .mock import MockCRM

__all__ = ["CRMAdapter", "CRMError", "HubSpotCRM", "MockCRM"]
