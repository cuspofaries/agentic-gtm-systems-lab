"""Load the GTM context from a directory of YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import GTMContext

CONTEXT_FILENAME = "gtm-context.yaml"


def load_context(path: str | Path) -> GTMContext:
    """Load a GTMContext from a YAML file or a directory containing `gtm-context.yaml`."""
    p = Path(path)
    if p.is_dir():
        p = p / CONTEXT_FILENAME
    if not p.exists():
        raise FileNotFoundError(f"GTM context not found at {p}")
    data = yaml.safe_load(p.read_text())
    if data is None:
        raise ValueError(f"GTM context at {p} is empty")
    return GTMContext.model_validate(data)
