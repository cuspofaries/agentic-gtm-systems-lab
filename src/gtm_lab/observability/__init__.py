"""Event recorder - audit trail for every workflow decision and side effect."""

from .events import EventRecorder, NullRecorder

__all__ = ["EventRecorder", "NullRecorder"]
