"""Typer CLI entry point. 6 commands.

- demo     - run an end-to-end offline demo end to end, stops before CRM writes
- eval     - run evaluation checks on a run id
- runs     - list runs
- inspect  - print a JSON dump of a run's state
- approve  - mark a run's plan as approved
- reject   - mark a run's plan as rejected
- sync     - apply an approved plan to the mock CRM
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .agents.qualifier import DeterministicQualifier, LLMQualifier
from .context import load_context
from .crm.mock import MockCRM
from .evaluations import evaluate_run
from .observability.events import EventRecorder
from .persistence.store import RunStore
from .providers import FixtureDiscoveryProvider, FixtureEnrichmentProvider
from .review import render_review
from .workflows import AccountDiscoveryWorkflow

app = typer.Typer(add_completion=False, help="Agentic GTM systems lab.")
console = Console()


DEFAULT_STATE_DIR = Path(os.environ.get("GTM_LAB_STATE_DIR", "./.gtm-lab-state"))
DEFAULT_EXAMPLE = Path(__file__).resolve().parent.parent.parent / "examples" / "account-discovery-to-crm"


def _build_workflow(
    example_dir: Path, state_dir: Path, fallback_qualifier=None
) -> AccountDiscoveryWorkflow:
    state_dir.mkdir(parents=True, exist_ok=True)
    ctx = load_context(example_dir / "config" / "gtm-context.yaml")
    disc = FixtureDiscoveryProvider(example_dir / "fixtures" / "discovery.yaml")
    enrich = FixtureEnrichmentProvider(example_dir / "fixtures" / "enrichment.yaml")
    crm = MockCRM(state_dir / "crm.sqlite")
    store = RunStore(state_dir / "runs.sqlite")
    recorder = EventRecorder(state_dir / "events.jsonl")
    _seed_crm_if_empty(crm, example_dir / "fixtures" / "crm-seed.yaml")
    return AccountDiscoveryWorkflow(
        context=ctx,
        discovery=disc,
        enrichment=enrich,
        crm=crm,
        store=store,
        recorder=recorder,
        qualifier=DeterministicQualifier(),
        fallback_qualifier=fallback_qualifier,
    )


def _seed_crm_if_empty(crm: MockCRM, seed_path: Path) -> None:
    import yaml

    if crm.all_accounts():
        return
    if not seed_path.exists():
        return
    data = yaml.safe_load(seed_path.read_text()) or {}
    for row in data.get("accounts", []):
        crm.create_account(
            domain=row["domain"],
            name=row["name"],
            source=row.get("source", "seed"),
            external_ref=row["external_ref"],
        )


def _anthropic_credentials_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").is_dir()


@app.command()
def demo(
    offline: bool = typer.Option(True, "--offline/--agentic", help="Offline deterministic demo (default)."),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir", help="Path to the example workflow."),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir", help="Where to persist run state."),
    limit: int = typer.Option(50, "--limit", help="Max accounts requested from the discovery provider."),
    max_llm_calls: int = typer.Option(5, "--max-llm-calls", help="Hard cap on LLM calls (agentic mode)."),
    max_llm_cents: int = typer.Option(50, "--max-llm-cents", help="Hard cap on LLM spend in cents."),
):
    """Run the demo end to end. Stops at the human approval gate - no CRM writes."""
    provider = None
    fallback = None
    if not offline and not _anthropic_credentials_available():
        console.print("[yellow]--agentic requested but no Anthropic credentials found "
                      "(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile). "
                      "Running the deterministic offline mode instead; no LLM was called.[/yellow]")
        offline = True
    if not offline:
        try:
            from .agents.anthropic_provider import AnthropicProviderCall

            provider = AnthropicProviderCall(max_calls=max_llm_calls, max_spend_cents=max_llm_cents)
            fallback = LLMQualifier(provider_call=provider)
            console.print(f"[cyan]Agentic mode:[/cyan] {provider.model} decides only the accounts "
                          f"the rules return as insufficient_evidence "
                          f"(cap {max_llm_calls} calls / {max_llm_cents}¢).")
        except Exception as exc:  # missing package or credentials
            console.print(f"[yellow]Agentic mode unavailable ({type(exc).__name__}: {exc}). "
                          "Running the deterministic offline mode instead; no LLM was called.[/yellow]")

    wf = _build_workflow(example_dir, state_dir, fallback_qualifier=fallback)
    plan = wf.plan(limit=limit)
    review_md = render_review(plan, wf.store)
    review_path = state_dir / f"review_{plan.run_id}.md"
    review_path.write_text(review_md)
    console.print(f"[green]Plan proposed:[/green] run_id=[bold]{plan.run_id}[/bold]")
    console.print(f"Review artifact → {review_path}")
    console.print("Next: `gtm-lab approve " + plan.run_id + "` or `gtm-lab reject " + plan.run_id + "`")
    t = Table(title="Entries")
    t.add_column("Account")
    t.add_column("Action")
    t.add_column("Contacts")
    t.add_column("Enrichment")
    t.add_column("Policy failures")
    for e in plan.entries:
        t.add_row(
            e.account_id,
            "skipped" if e.skipped else e.crm_action,
            str(len(e.contacts_to_create)),
            e.enrichment_status,
            ", ".join(e.policy_failures) or "-",
        )
    console.print(t)

    if provider is not None:
        cmp = Table(title="LLM vs deterministic baseline (insufficient_evidence cases only)")
        cmp.add_column("Account")
        cmp.add_column("Baseline")
        cmp.add_column("LLM")
        for ev in wf.recorder.read(plan.run_id):
            if ev["event_type"] == "qualification.fallback":
                pl = ev["payload"]
                cmp.add_row(pl["account_id"], pl["baseline_decision"], pl["fallback_decision"])
        console.print(cmp)
        console.print(f"LLM calls: {provider.calls} · estimated spend: {provider.spent_cents}¢"
                      + (f" · errors: {provider.errors}" if provider.errors else ""))


@app.command("eval")
def eval_cmd(
    run_id: str = typer.Argument(..., help="Run id to evaluate."),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Run evaluation checks against a completed run."""
    store = RunStore(state_dir / "runs.sqlite")
    report = evaluate_run(run_id, store)
    console.print(f"[bold]Evaluation of run[/bold] {run_id}")
    console.print(f"Status: [{'green' if report.ok else 'red'}]{'OK' if report.ok else 'FAIL'}[/]")
    for p in report.checks_passed:
        console.print(f"  ✅ {p}")
    for f in report.failures:
        console.print(f"  ❌ {f}")
    console.print("\n[bold]Metrics:[/bold]")
    for k, v in report.metrics.items():
        console.print(f"  {k}: {v}")
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def runs(state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir")):
    """List known runs."""
    store = RunStore(state_dir / "runs.sqlite")
    for rid in store.list_runs():
        console.print(rid)


@app.command()
def inspect(
    run_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Print a run's state as JSON."""
    store = RunStore(state_dir / "runs.sqlite")
    data = store.inspect(run_id)
    console.print_json(data=data, default=str)


@app.command()
def approve(
    run_id: str = typer.Argument(...),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Mark a proposed plan as approved. Does not sync."""
    wf = _build_workflow(example_dir, state_dir)
    plan = wf.approve(run_id)
    console.print(f"[green]Approved[/green] {run_id} ({len(plan.approvable_entries)} entries to sync)")


@app.command()
def reject(
    run_id: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason"),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Mark a proposed plan as rejected."""
    wf = _build_workflow(example_dir, state_dir)
    wf.reject(run_id, reason=reason)
    console.print(f"[yellow]Rejected[/yellow] {run_id}")


@app.command()
def sync(
    run_id: str = typer.Argument(...),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Apply an approved plan to the mock CRM. Idempotent: re-running does not duplicate."""
    wf = _build_workflow(example_dir, state_dir)
    results = wf.sync(run_id)
    oks = sum(1 for r in results if r.outcome == "ok")
    fails = sum(1 for r in results if r.outcome == "failed")
    noops = sum(1 for r in results if r.outcome == "idempotent_noop")
    console.print(f"Sync complete: {oks} ok · {fails} failed · {noops} noop")
    console.print_json(data=[json.loads(r.model_dump_json()) for r in results[:20]], default=str)


if __name__ == "__main__":
    app()
