"""End-to-end run on real data, checked against expected results.

Opt-in and private: the test code is public, the data is not. Point
`GTM_LAB_REAL_CASE_DIR` at a directory kept out of the repository that holds:

    config/gtm-context.yaml   your ICP, personas and budget
    expected.yaml             the run settings and what the run must produce
    cassette.json             recorded provider exchanges (written by record mode)

Modes (`GTM_LAB_REAL_CASE_MODE`):

- `replay` (default): free, offline. Every provider answer comes from the cassette.
- `record`: calls People Data Labs, FullEnrich, ZeroBounce and HubSpot for real and
  spends credits. Needs their keys. Writes the cassette.

Both modes write `observed.yaml`, the run reduced to the values `expect:` can check.

HubSpot is only read: the test plans, it never approves or syncs, and it fails if the
plan made any CRM call that changes data.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from .harness import KEY_VARS, OPTIONAL_KEY_VARS, REPLAY_KEYS, run_case

CASE_DIR = os.environ.get("GTM_LAB_REAL_CASE_DIR")
MODE = os.environ.get("GTM_LAB_REAL_CASE_MODE", "replay")

pytestmark = [
    pytest.mark.real_case,
    pytest.mark.skipif(not CASE_DIR, reason="set GTM_LAB_REAL_CASE_DIR to run the real-data case"),
]


def _keys() -> dict[str, str]:
    if MODE == "replay":
        return dict(REPLAY_KEYS)
    keys = {v: os.environ.get(v, "") for v in KEY_VARS}
    if not keys["HUBSPOT_SERVICE_KEY"]:
        keys["HUBSPOT_SERVICE_KEY"] = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
    keys |= {v: os.environ[v] for v in OPTIONAL_KEY_VARS if os.environ.get(v)}
    missing = [v for v, k in keys.items() if not k]
    if missing:
        pytest.fail(f"record mode needs {', '.join(missing)}")
    return keys


def test_real_case_matches_expected(tmp_path):
    _observed, diffs = run_case(Path(CASE_DIR), MODE, _keys(), tmp_path)
    if diffs is None:
        pytest.fail("no `expect:` section yet; review observed.yaml and copy what is right into expected.yaml")
    assert not diffs, yaml.safe_dump(diffs, sort_keys=False)
