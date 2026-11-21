"""Policy checks run on every WritePlanEntry before approval is offered."""

from .rules import evaluate_entry

__all__ = ["evaluate_entry"]
