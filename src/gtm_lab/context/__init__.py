"""GTM context layer - versioned, structured, agent-readable business knowledge."""

from .loader import load_context
from .models import (
    BuyerPersona,
    CRMSettings,
    DiscoverySettings,
    ExclusionRule,
    GTMContext,
    ICPBand,
    Policy,
    SignalPolicy,
)

__all__ = [
    "BuyerPersona",
    "CRMSettings",
    "DiscoverySettings",
    "ExclusionRule",
    "GTMContext",
    "ICPBand",
    "Policy",
    "SignalPolicy",
    "load_context",
]
