"""CRM abstraction + SQLite-backed mock implementation."""

from .base import CRMAdapter, CRMError
from .mock import MockCRM

__all__ = ["CRMAdapter", "CRMError", "MockCRM"]
