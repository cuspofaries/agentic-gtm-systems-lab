"""Claude Code integration: static checks on the skill and CLI behavior it relies on.

These tests do not run Claude Code. They check that the skill only refers to real
commands and flags, only pre-authorizes read-only commands, and that the CLI keeps
approval mandatory and reads persisted state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from gtm_lab.cli import app
from gtm_lab.crm.mock import MockCRM
from gtm_lab.persistence.store import RunStore

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "gtm-lab" / "SKILL.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
STATE_CHANGING = {"approve", "reject", "sync"}

runner = CliRunner()


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    _, fm, body = text.split("---\n", 2)
    return yaml.safe_load(fm), body


def _cli_commands() -> dict[str, set[str]]:
    group = typer.main.get_command(app)
    return {
        name: {opt for p in cmd.params for opt in (*p.opts, *p.secondary_opts)}
        for name, cmd in group.commands.items()
    }


def _gtm_lab_invocations(text: str) -> list[tuple[str, list[str]]]:
    """Every CLI call `gtm-lab <cmd> ...` (or `.venv/bin/gtm-lab ...`) in the text.

    Skill invocations such as `/gtm-lab explain` are not CLI calls and are skipped.
    """
    out = []
    pattern = r"(?:(?<=[\s`(])|^)(?:\.venv/bin/)?gtm-lab ([a-z]+)([^`)\n]*)"
    for m in re.finditer(pattern, text, re.M):
        out.append((m.group(1), re.findall(r"--[a-z][a-z-]*", m.group(2))))
    return out


# --- skill file -------------------------------------------------------------


@pytest.mark.integration
def test_skill_file_location_and_frontmatter():
    assert SKILL.is_file()
    fm, body = _frontmatter(SKILL)
    assert fm["name"] == "gtm-lab"
    assert fm["description"].strip()
    assert fm["argument-hint"].strip()
    assert fm["disable-model-invocation"] is True  # user-invoked only
    assert "$ARGUMENTS" in body
    assert len(SKILL.read_text().splitlines()) < 500


@pytest.mark.integration
@pytest.mark.parametrize("path", [SKILL, CLAUDE_MD])
def test_docs_only_reference_real_commands_and_flags(path):
    commands = _cli_commands()
    found = _gtm_lab_invocations(path.read_text())
    assert found, f"no gtm-lab commands found in {path.name}"
    for cmd, flags in found:
        assert cmd in commands, f"{path.name} mentions unknown command `gtm-lab {cmd}`"
        for flag in flags:
            assert flag in commands[cmd], f"{path.name}: `gtm-lab {cmd}` has no option {flag}"


@pytest.mark.integration
def test_skill_never_preauthorizes_state_changes_or_paid_calls():
    fm, _ = _frontmatter(SKILL)
    allowed = fm["allowed-tools"]
    bash_rules = [r for r in allowed if r.startswith("Bash(")]
    for rule in bash_rules:
        for cmd in STATE_CHANGING:
            assert f"gtm-lab {cmd}" not in rule, rule
        assert "--agentic" not in rule, rule
    demo_rules = [r for r in bash_rules if "demo" in r]
    # exact match, no wildcard: `demo --offline *` would also allow `--offline --agentic`
    assert demo_rules and all(r.endswith("demo --offline --json)") for r in demo_rules)


@pytest.mark.integration
def test_claude_md_states_the_safety_rules():
    text = CLAUDE_MD.read_text()
    assert "/gtm-lab" in text
    assert "Never approve, reject or sync" in text
    assert "separate steps" in text


# --- CLI behavior the skill depends on ---------------------------------------


def _demo(state_dir: Path) -> dict:
    res = runner.invoke(app, ["demo", "--offline", "--json", "--state-dir", str(state_dir)])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


@pytest.mark.integration
def test_demo_json_is_offline_and_writes_nothing(tmp_path, monkeypatch):
    import gtm_lab.agents.anthropic_provider as ap

    def no_llm(*_a, **_k):
        raise AssertionError("offline demo must not build an LLM provider")

    monkeypatch.setattr(ap, "AnthropicProviderCall", no_llm)
    summary = _demo(tmp_path)
    assert summary["mode"] == "offline"
    assert summary["llm_calls_made"] == 0
    assert summary["plan_status"] == "proposed"
    assert summary["review_artifact_exists"]
    assert Path(summary["paths"]["review_artifact"]).is_file()
    assert summary["writes"] == {"ok": 0, "idempotent_noop": 0, "failed": 0, "stale_plan": 0}
    assert [a.domain for a in MockCRM(tmp_path / "crm.sqlite").all_accounts()] == [
        "axiom.saas.example"  # only the seed
    ]


@pytest.mark.integration
def test_inspect_reads_persisted_state(tmp_path):
    run_id = _demo(tmp_path)["run_id"]
    res = runner.invoke(app, ["inspect", run_id, "--state-dir", str(tmp_path)])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    store = RunStore(tmp_path / "runs.sqlite")
    assert {d["account_id"]: d["decision"] for d in data["decisions"]} == {
        d.account_id: d.decision for d in store.load_decisions(run_id)
    }
    # historical keys kept for existing integrations
    assert {"run_id", "accounts", "decisions", "plan", "write_results"} <= set(data)
    assert {e["event_type"] for e in data["events"]} >= {"workflow.start", "plan.proposed"}


@pytest.mark.integration
def test_sync_refused_without_approval(tmp_path):
    run_id = _demo(tmp_path)["run_id"]
    res = runner.invoke(app, ["sync", run_id, "--state-dir", str(tmp_path)])
    assert res.exit_code == 1
    assert "must be approved" in res.output
    assert len(MockCRM(tmp_path / "crm.sqlite").all_accounts()) == 1


@pytest.mark.integration
def test_rejected_run_cannot_be_synced_or_approved(tmp_path):
    run_id = _demo(tmp_path)["run_id"]
    assert runner.invoke(app, ["reject", run_id, "--state-dir", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["sync", run_id, "--state-dir", str(tmp_path)]).exit_code == 1
    assert runner.invoke(app, ["approve", run_id, "--state-dir", str(tmp_path)]).exit_code == 1
    assert len(MockCRM(tmp_path / "crm.sqlite").all_accounts()) == 1


@pytest.mark.integration
def test_unknown_run_gives_clean_error(tmp_path):
    for cmd in ("inspect", "eval", "approve", "reject", "sync"):
        res = runner.invoke(app, [cmd, "nope", "--state-dir", str(tmp_path)])
        assert res.exit_code == 1, cmd
        assert "No run 'nope'" in res.output, cmd


@pytest.mark.integration
def test_full_lifecycle_through_cli_is_idempotent(tmp_path):
    run_id = _demo(tmp_path)["run_id"]
    sd = ["--state-dir", str(tmp_path)]
    approved = runner.invoke(app, ["approve", run_id, *sd])
    assert approved.exit_code == 0 and "Nothing has been written" in approved.output
    first = json.loads(runner.invoke(app, ["sync", run_id, "--json", *sd]).stdout)
    assert first["crm_target"] == "local mock CRM (SQLite)"
    assert (first["status_before"], first["status_after"]) == ("approved", "synced")
    assert first["counts"] == {"ok": 12, "idempotent_noop": 2, "failed": 0, "stale_plan": 0}
    second = json.loads(runner.invoke(app, ["sync", run_id, "--json", *sd]).stdout)
    assert second["counts"] == {"ok": 0, "idempotent_noop": 14, "failed": 0, "stale_plan": 0}
    report = json.loads(runner.invoke(app, ["eval", run_id, "--json", *sd]).stdout)
    assert report["ok"] is True
    runs = json.loads(runner.invoke(app, ["runs", "--json", *sd]).stdout)
    assert runs[0]["run_id"] == run_id and runs[0]["plan_status"] == "synced"


@pytest.mark.integration
def test_runs_default_output_unchanged(tmp_path):
    run_id = _demo(tmp_path)["run_id"]
    res = runner.invoke(app, ["runs", "--state-dir", str(tmp_path)])
    assert res.stdout.strip().splitlines() == [run_id]
