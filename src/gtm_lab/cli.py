"""Typer CLI entry point.

- demo     - run the workflow end to end, stop at the approval gate (no CRM writes)
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
from .crm.base import CRMError
from .crm.hubspot import HubSpotCRM
from .crm.mock import MockCRM
from .evaluations import evaluate_run
from .observability.events import EventRecorder
from .persistence.store import RunStore
from .providers import (
    FixtureDiscoveryProvider,
    FixtureEnrichmentProvider,
    PDLCompanyDiscoveryProvider,
    PDLEnrichmentProvider,
)
from .review import render_review
from .summary import CRM_LABELS, run_detail, run_summary
from .workflows import AccountDiscoveryWorkflow

app = typer.Typer(add_completion=False, help="Agentic GTM systems lab.")
console = Console()
err_console = Console(stderr=True)


DEFAULT_STATE_DIR = Path(os.environ.get("GTM_LAB_STATE_DIR", "./.gtm-lab-state"))
DEFAULT_EXAMPLE = Path(__file__).resolve().parent.parent.parent / "examples" / "account-discovery-to-crm"


HUBSPOT_TOKEN_VARS = ("HUBSPOT_SERVICE_KEY", "HUBSPOT_ACCESS_TOKEN")


def _make_crm(crm: str, ctx, state_dir: Path):
    if crm == "mock":
        return MockCRM(state_dir / "crm.sqlite")
    if crm == "hubspot":
        token = next((os.environ[v] for v in HUBSPOT_TOKEN_VARS if os.environ.get(v)), None)
        if not token:
            _fail("HubSpot selected but no token found.",
                  f"Export {HUBSPOT_TOKEN_VARS[0]} (a HubSpot Service Key). See docs/hubspot.md.")
        return HubSpotCRM(token, contact_legal_basis=ctx.crm.hubspot_contact_legal_basis)
    _fail(f"Unknown CRM '{crm}'. Use 'mock' or 'hubspot'.")


def _make_enrichment(enrichment: str, ctx, example_dir: Path, profiles_per_account: int,
                     title_levels: list[str] | None = None):
    if enrichment == "fixture":
        return FixtureEnrichmentProvider(example_dir / "fixtures" / "enrichment.yaml")
    if enrichment == "pdl":
        key = os.environ.get("PDL_API_KEY")
        if not key:
            _fail("People Data Labs selected but PDL_API_KEY is not set.")
        return PDLEnrichmentProvider(key, ctx.personas, profiles_per_account=profiles_per_account,
                                     title_levels=title_levels)
    _fail(f"Unknown enrichment provider '{enrichment}'. Use 'fixture' or 'pdl'.")


def _make_discovery(discovery: str, ctx, example_dir: Path):
    if discovery == "fixture":
        return FixtureDiscoveryProvider(example_dir / "fixtures" / "discovery.yaml")
    if discovery == "pdl":
        key = os.environ.get("PDL_API_KEY")
        if not key:
            _fail("People Data Labs selected but PDL_API_KEY is not set.")
        return PDLCompanyDiscoveryProvider(key, ctx)
    _fail(f"Unknown discovery provider '{discovery}'. Use 'fixture' or 'pdl'.")


def _build_workflow(
    example_dir: Path,
    state_dir: Path,
    fallback_qualifier=None,
    crm: str = "mock",
    discovery: str = "fixture",
    enrichment: str = "fixture",
    profiles_per_account: int = 2,
    title_levels: list[str] | None = None,
) -> AccountDiscoveryWorkflow:
    state_dir.mkdir(parents=True, exist_ok=True)
    ctx = load_context(example_dir / "config" / "gtm-context.yaml")
    return AccountDiscoveryWorkflow(
        context=ctx,
        discovery=_make_discovery(discovery, ctx, example_dir),
        enrichment=_make_enrichment(enrichment, ctx, example_dir, profiles_per_account, title_levels),
        crm=_make_crm(crm, ctx, state_dir),
        store=RunStore(state_dir / "runs.sqlite"),
        recorder=EventRecorder(state_dir / "events.jsonl"),
        qualifier=DeterministicQualifier(),
        fallback_qualifier=fallback_qualifier,
    )


def _state_workflow(state_dir: Path) -> AccountDiscoveryWorkflow:
    """Approve and reject only change persisted plan state: no provider, CRM or context."""
    return AccountDiscoveryWorkflow(
        context=None,
        discovery=None,
        enrichment=None,
        crm=None,
        store=RunStore(state_dir / "runs.sqlite"),
        recorder=EventRecorder(state_dir / "events.jsonl"),
    )


def _sync_workflow(example_dir: Path, state_dir: Path, crm: str) -> AccountDiscoveryWorkflow:
    """Sync writes a stored plan: it needs the context (CRM settings) and the CRM, not the
    discovery or enrichment providers."""
    ctx = load_context(example_dir / "config" / "gtm-context.yaml")
    return AccountDiscoveryWorkflow(
        context=ctx,
        discovery=None,
        enrichment=None,
        crm=_make_crm(crm, ctx, state_dir),
        store=RunStore(state_dir / "runs.sqlite"),
        recorder=EventRecorder(state_dir / "events.jsonl"),
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
    as_json: bool = typer.Option(False, "--json", help="Print a structured run summary as JSON."),
    crm: str = typer.Option("mock", "--crm", help="CRM to reconcile against: mock (default) or hubspot."),
    discovery: str = typer.Option("fixture", "--discovery", help="Company source: fixture (default) or pdl."),
    enrichment: str = typer.Option("fixture", "--enrichment", help="Contact provider: fixture (default) or pdl."),
    profiles_per_account: int = typer.Option(2, "--profiles-per-account", help="pdl only: max contacts fetched per account."),
    title_levels: str = typer.Option("", "--title-levels", help="pdl only: comma-separated job_title_levels to keep, e.g. cxo,vp,director."),
):
    """Run the demo end to end. Stops at the human approval gate - no CRM writes."""
    out = err_console if as_json else console
    provider = None
    fallback = None
    if not offline and not _anthropic_credentials_available():
        out.print("[yellow]--agentic requested but no Anthropic credentials found "
                  "(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile). "
                  "Running the deterministic offline mode instead; no LLM was called.[/yellow]")
        offline = True
    if not offline:
        try:
            from .agents.anthropic_provider import AnthropicProviderCall

            provider = AnthropicProviderCall(max_calls=max_llm_calls, max_spend_cents=max_llm_cents)
            fallback = LLMQualifier(provider_call=provider)
            out.print(f"[cyan]Agentic mode:[/cyan] {provider.model} decides only the accounts "
                      f"the rules return as insufficient_evidence "
                      f"(cap {max_llm_calls} calls / {max_llm_cents}¢).")
        except Exception as exc:  # missing package or credentials
            out.print(f"[yellow]Agentic mode unavailable ({type(exc).__name__}: {exc}). "
                      "Running the deterministic offline mode instead; no LLM was called.[/yellow]")

    wf = _build_workflow(example_dir, state_dir, fallback_qualifier=fallback, crm=crm,
                         discovery=discovery, enrichment=enrichment, profiles_per_account=profiles_per_account,
                         title_levels=[x.strip() for x in title_levels.split(",") if x.strip()] or None)
    if enrichment != "fixture":
        out.print(f"[cyan]Enrichment:[/cyan] {wf.enrichment.name}, up to "
                  f"{wf.enrichment.max_cost_cents_per_call}¢ per account, "
                  f"budget {wf.context.policy.enrichment_budget_cents}¢.")
    if crm == "mock":
        _seed_crm_if_empty(wf.crm, example_dir / "fixtures" / "crm-seed.yaml")
    else:
        out.print(f"[cyan]CRM:[/cyan] reconciling against {CRM_LABELS[crm]} (read-only until sync).")
    try:
        plan = wf.plan(limit=limit)
    except CRMError as e:
        _fail(f"CRM error while planning against {CRM_LABELS[crm]}: {e}")
    review_path = state_dir / f"review_{plan.run_id}.md"
    review_path.write_text(render_review(plan, wf.store))

    credits = getattr(wf.enrichment, "credits_remaining", None)
    if as_json:
        summary = run_summary(plan.run_id, state_dir)
        if credits is not None:
            summary["provider_credits_remaining"] = credits
        if provider is not None:
            summary["llm"] = {"calls": provider.calls, "estimated_spend_cents": provider.spent_cents,
                              "errors": provider.errors}
        _emit_json(summary)
        return

    console.print(f"[green]Plan proposed:[/green] run_id=[bold]{plan.run_id}[/bold]")
    if credits is not None:
        console.print(f"{wf.enrichment.name} credits remaining: {credits}")
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


def _emit_json(data) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _fail(message: str, hint: str = "") -> None:
    err_console.print(f"[red]Error:[/red] {message}")
    if hint:
        err_console.print(hint)
    raise typer.Exit(code=1)


def _require_plan(run_id: str, state_dir: Path):
    plan = RunStore(state_dir / "runs.sqlite").load_plan(run_id)
    if plan is None:
        _fail(f"No run '{run_id}' in {state_dir}.", "List runs with `gtm-lab runs`.")
    return plan


@app.command("eval")
def eval_cmd(
    run_id: str = typer.Argument(..., help="Run id to evaluate."),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
):
    """Run the four integrity checks against a run. Exit code 1 if any check fails."""
    _require_plan(run_id, state_dir)
    store = RunStore(state_dir / "runs.sqlite")
    report = evaluate_run(run_id, store)
    if as_json:
        _emit_json({"run_id": run_id, "ok": report.ok, "checks_passed": report.checks_passed,
                    "failures": report.failures, "metrics": report.metrics})
        raise typer.Exit(code=0 if report.ok else 1)
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
def runs(
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
    as_json: bool = typer.Option(False, "--json", help="Print run ids with status as JSON."),
):
    """List known runs, newest first."""
    store = RunStore(state_dir / "runs.sqlite")
    ids = store.list_runs()
    if as_json:
        out = []
        for rid in ids:
            plan = store.load_plan(rid)
            out.append({"run_id": rid, "plan_status": plan.status.value,
                        "created_at": plan.created_at, "entries": len(plan.entries)})
        _emit_json(out)
        return
    for rid in ids:
        console.print(rid)


@app.command()
def inspect(
    run_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Print everything persisted for a run as JSON: summary, records, audit events."""
    data = run_detail(run_id, state_dir)
    if data is None:
        _fail(f"No run '{run_id}' in {state_dir}.", "List runs with `gtm-lab runs`.")
    _emit_json(data)


@app.command()
def approve(
    run_id: str = typer.Argument(...),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Mark a proposed plan as approved. Writes nothing to the CRM."""
    before = _require_plan(run_id, state_dir).status.value
    if before != "proposed":
        _fail(f"Run {run_id} is '{before}'; only a 'proposed' plan can be approved.")
    plan = _state_workflow(state_dir).approve(run_id)
    console.print(f"[green]Approved[/green] {run_id}: proposed → approved. "
                  f"{len(plan.approvable_entries)} entries will be written by `gtm-lab sync {run_id}`. "
                  "Nothing has been written to the CRM yet.")


@app.command()
def reject(
    run_id: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason"),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
):
    """Mark a proposed plan as rejected. Nothing will be written for this run."""
    before = _require_plan(run_id, state_dir).status.value
    if before != "proposed":
        _fail(f"Run {run_id} is '{before}'; only a 'proposed' plan can be rejected.")
    _state_workflow(state_dir).reject(run_id, reason=reason)
    console.print(f"[yellow]Rejected[/yellow] {run_id}: proposed → rejected.")


@app.command()
def sync(
    run_id: str = typer.Argument(...),
    example_dir: Path = typer.Option(DEFAULT_EXAMPLE, "--example-dir"),
    state_dir: Path = typer.Option(DEFAULT_STATE_DIR, "--state-dir"),
    as_json: bool = typer.Option(False, "--json", help="Print every write result as JSON."),
):
    """Apply an approved plan to the CRM it was planned against. Idempotent."""
    plan_before = _require_plan(run_id, state_dir)
    before = plan_before.status.value
    target = plan_before.crm_target
    if before in {"proposed", "draft"}:
        _fail(f"Run {run_id} is '{before}': it must be approved before it can be synced.",
              f"Review it, then `gtm-lab approve {run_id}`.")
    if before == "rejected":
        _fail(f"Run {run_id} was rejected; it cannot be synced.")
    wf = _sync_workflow(example_dir, state_dir, target)
    results = wf.sync(run_id)
    after = wf.store.load_plan(run_id).status.value
    counts = {k: sum(1 for r in results if r.outcome == k)
              for k in ("ok", "idempotent_noop", "failed", "stale_plan")}
    if as_json:
        _emit_json({"run_id": run_id, "crm_target": CRM_LABELS.get(target, target),
                    "crm_db": str(state_dir / "crm.sqlite") if target == "mock" else None,
                    "status_before": before,
                    "status_after": after, "counts": counts,
                    "results": [json.loads(r.model_dump_json()) for r in results]})
        return
    where = f" ({state_dir / 'crm.sqlite'})" if target == "mock" else ""
    console.print(f"Target: {CRM_LABELS.get(target, target)}{where}. Plan: {before} → {after}.")
    console.print(f"Sync complete: {counts['ok']} ok · {counts['failed']} failed · "
                  f"{counts['idempotent_noop']} noop · {counts['stale_plan']} stale")
    console.print_json(data=[json.loads(r.model_dump_json()) for r in results[:20]], default=str)


if __name__ == "__main__":
    app()
