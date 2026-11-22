"""The learning path (docs/learning-path.md) stays executable.

Each test replays one module's commands offline, in a temporary state directory, against
the mock CRM and the committed recordings, and checks the outcome the page tells the
reader to expect. Two meta-tests check that every `gtm-lab` command and every
`pytest ... -k` selection quoted on the page still exists.

Manual steps not automated here: the shell variable plumbing (`RUN=$(...)`) and `cat` of
the review artifact; the commands behind them are exercised directly.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from gtm_lab.cli import app

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "learning-path.md").read_text()
EXAMPLE = ROOT / "examples" / "account-discovery-to-crm"
FRESH = ["--cases", str(ROOT / "evals/decisions/fresh.yaml"), "--cassette", str(ROOT / "evals/decisions/fresh-cassette.json")]

pytestmark = pytest.mark.integration


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("GTM_LAB_STATE_DIR", str(tmp_path / "state"))
    runner = CliRunner()

    state = str(tmp_path / "state")

    def run(*args, ok=True):
        args = list(args)
        # The page exports GTM_LAB_STATE_DIR, read when the CLI starts; in-process the
        # default is already bound, so the state directory is passed explicitly.
        if args[0] in ("demo", "inspect", "approve", "sync", "eval", "runs", "opportunity", "reject"):
            args += ["--state-dir", state]
        res = runner.invoke(app, args)
        if ok:
            assert res.exit_code == 0, res.output
        return res

    run.tmp = tmp_path
    return run


def _json(res):
    out = res.output
    return json.loads(out[out.index("{"):])


def _demo(cli, *extra):
    return _json(cli("demo", "--offline", "--json", *extra))["run_id"]


def _inspect(cli, run_id):
    return _json(cli("inspect", run_id))


def _copy_example(tmp: Path, name: str) -> Path:
    dst = tmp / name
    shutil.copytree(EXAMPLE, dst)
    return dst


def _edit_case(fixture: Path, account_id: str, **fields):
    data = yaml.safe_load(fixture.read_text())
    row = next(r for r in data["accounts"] if r["account_id"] == account_id)
    row.update(fields)
    fixture.write_text(yaml.safe_dump(data, sort_keys=False))


# --- meta: the page only quotes things that exist -------------------------------------


def test_every_quoted_gtm_lab_command_and_option_exists():
    # Read the options from the command definitions, not from --help (whose rendering
    # depends on the terminal: colors and wrapping differ in CI).
    import typer

    group = typer.main.get_command(app)
    commands = re.findall(r"^\s*gtm-lab ([a-z-]+)((?: [^\n|#]*)?)", DOC, flags=re.M)
    assert commands
    for sub, rest in commands:
        cmd = group.commands.get(sub)
        assert cmd is not None, f"gtm-lab {sub} does not exist"
        known = {o for p in cmd.params for o in (*p.opts, *p.secondary_opts)}
        for opt in re.findall(r"(--[a-z][a-z-]+)", rest):
            assert opt in known, f"gtm-lab {sub} has no option {opt}"


def test_every_quoted_pytest_selection_selects_something():
    selections = re.findall(r"`pytest (tests/[^`]+)`", DOC)  # runnable selections, not prose
    assert selections
    for sel in selections:
        if sel.strip() in ("-q",):
            continue
        args = re.findall(r'"[^"]*"|\S+', sel)
        args = [a.strip('"') for a in args]
        out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
                             cwd=ROOT, capture_output=True, text=True)
        # pytest exits 5 when nothing is collected (including everything deselected by -k).
        assert out.returncode == 0, f"no test for `pytest {sel}`: {out.stdout[-300:]}"


# --- module 1: Data Foundation ---------------------------------------------------------


def test_module_1_unknowns_identity_and_duplicates(cli):
    run = _demo(cli)
    d = _inspect(cli, run)
    a = next(x for x in d["accounts"] if x["account_id"] == "acc_005")
    assert a["employees"] is None and a["fields_missing"] == ["employees", "hq_state"]
    assert a["source"]["provider"] == "fixture-discovery"
    assert next(x for x in d["decisions"] if x["account_id"] == "acc_005")["decision"] == "insufficient_evidence"

    ex = _copy_example(cli.tmp, "my-example")
    _edit_case(ex / "fixtures/discovery.yaml", "acc_005", employees=120, fields_missing=[])
    d2 = _inspect(cli, _demo(cli, "--example-dir", str(ex)))
    assert next(x for x in d2["decisions"] if x["account_id"] == "acc_005")["decision"] == "qualified"

    _edit_case(ex / "fixtures/discovery.yaml", "acc_005", domain="WWW.Brightpath.saas.example/")
    d3 = _inspect(cli, _demo(cli, "--example-dir", str(ex)))
    entry = next(e for e in d3["plan"]["entries"] if e["account_id"] == "acc_005")
    assert entry["skip_reason"] == "Ambiguous CRM match (candidates: ['acc_001'])"


# --- module 2: Data Layer -------------------------------------------------------------------


def test_module_2_reconciliation_skips_existing_and_duplicates(cli):
    d = _inspect(cli, _demo(cli))
    skipped = {e["account_id"]: e["skip_reason"] for e in d["plan"]["entries"] if e["skipped"]}
    assert skipped["acc_003"] == "Already exists in CRM"
    assert skipped["acc_004_dup"].startswith("Ambiguous CRM match")


# --- module 3: GTM Brain ---------------------------------------------------------------------


def test_module_3_icp_change_and_brain_validation(cli):
    ex = _copy_example(cli.tmp, "brain-example")
    cfg = ex / "config/gtm-context.yaml"
    cfg.write_text(cfg.read_text().replace("  employees_max: 200", "  employees_max: 100", 1))
    d = _inspect(cli, _demo(cli, "--example-dir", str(ex)))
    decided = {x["account_id"]: x["decision"] for x in d["decisions"]}
    assert decided["acc_003"] == decided["acc_004"] == decided["acc_004_dup"] == "not_qualified"

    cfg.write_text(cfg.read_text().replace("    personas: [Sales Enablement]", "    personas: [Chief Vibes Officer]", 1))
    res = cli("demo", "--offline", "--example-dir", str(ex), ok=False)
    assert res.exit_code != 0
    assert "unknown pain point id(s) or persona name(s)" in str(res.exception) + res.output


# --- module 4: Agentic research (replayed) --------------------------------------------------


def test_module_4_replay_shows_a_supported_and_a_disconfirmed_case(cli):
    cli("opportunity-eval", *FRESH)
    from gtm_lab.context import load_context
    from gtm_lab.evaluations.opportunity_eval import LLMCassette, run_eval

    ctx = load_context(EXAMPLE / "config/gtm-context.yaml")
    run = run_eval(ROOT / "evals/decisions/fresh.yaml", ctx, LLMCassette(ROOT / "evals/decisions/fresh-cassette.json",
                                                                         "replay"))
    by = {r.case["id"]: r for r in run.results}
    cro, freeze = by["f-relevant-cro-quote"], by["f-abstain-freeze-search"]
    assert cro.agent.assessment_status == "evidence_backed" and cro.baseline.assessment_status == "evidence_backed"
    assert any(m.validation == "verified" and not m.lexical_term_hit for m in cro.agent.semantic_matches)
    assert any("not proof" in c.reason for c in cro.agent.disconfirmation_checks)
    assert freeze.agent.assessment_status == "needs_research" and freeze.baseline.assessment_status == "evidence_backed"
    assert any(c.credited and c.outcome_claimed == "found_contradiction" for c in freeze.agent.disconfirmation_checks)


def test_module_4_changing_a_label_moves_metrics_not_answers(cli, tmp_path):
    data = yaml.safe_load((ROOT / "evals/decisions/fresh.yaml").read_text())
    data["cases"][0]["label"]["relevant"] = False
    copy = tmp_path / "fresh-copy.yaml"
    copy.write_text(yaml.safe_dump(data, sort_keys=False))
    res = cli("opportunity-eval", "--json", "--cases", str(copy), "--cassette",
              str(ROOT / "evals/decisions/fresh-cassette.json"))
    assert _json(res)["misses"] == []


# --- module 5: Activation & Governance -------------------------------------------------------


def test_module_5_gated_offline_spends_nothing_then_approval_and_idempotent_sync(cli):
    d = _inspect(cli, _demo(cli, "--enrichment-strategy", "opportunity_gated"))
    new = [e for e in d["plan"]["entries"] if e["crm_action"] == "create_account"]
    assert new and all(e["enrichment_status"] == "deferred_by_policy" for e in new)
    assert {e["opportunity_action"]["assessment_status"] for e in new} == {"not_assessed"}
    cli("decision-eval", *FRESH)

    run = _demo(cli)
    review = Path(cli.tmp / "state" / f"review_{run}.md")
    assert review.exists() and "Approvable" in review.read_text()
    cli("approve", run)
    first = _json(cli("sync", run, "--json"))["counts"]
    second = _json(cli("sync", run, "--json"))["counts"]
    assert first["ok"] > 0 and first["failed"] == 0
    assert second["ok"] == 0 and second["idempotent_noop"] == first["ok"] + first["idempotent_noop"]


# --- module 6: Evaluation ------------------------------------------------------------------


def test_module_6_eval_replays_and_prompt_change_misses(cli, monkeypatch):
    run = _demo(cli)
    cli("eval", run)
    cli("decision-eval")
    cli("decision-eval", *FRESH)
    import gtm_lab.agents.opportunity as opp

    monkeypatch.setattr(opp, "SYSTEM_PROMPT", opp.SYSTEM_PROMPT.replace("B2B company", "B2B firm", 1))
    res = cli("decision-eval", ok=False)
    assert res.exit_code == 1 and "Replay misses" in res.output
