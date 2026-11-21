"""Render a human-readable review artifact for a proposed WritePlan.

This is the Markdown the approver reads before calling `gtm-lab approve <run-id>`.
Keep it skim-readable: headline at the top, details below.
"""

from __future__ import annotations

from .models import WritePlan
from .persistence.store import RunStore


def render_review(plan: WritePlan, store: RunStore) -> str:
    accounts = {a.account_id: a for a in store.load_accounts(plan.run_id)}
    decisions = {d.account_id: d for d in store.load_decisions(plan.run_id)}

    approvable = plan.approvable_entries
    blocked_policy = [e for e in plan.entries if e.policy_failures and not e.skipped]
    skipped_existing = [e for e in plan.entries if e.skipped and e.skip_reason == "Already exists in CRM"]
    skipped_ambiguous = [e for e in plan.entries if e.skipped and "Ambiguous" in (e.skip_reason or "")]

    lines: list[str] = []
    lines.append(f"# Review - run `{plan.run_id}`\n")
    lines.append(f"**Status:** {plan.status.value} · "
                 f"**Approvable:** {len(approvable)} · "
                 f"**Policy-blocked:** {len(blocked_policy)} · "
                 f"**Skipped (exists):** {len(skipped_existing)} · "
                 f"**Skipped (ambiguous):** {len(skipped_ambiguous)}")
    lines.append(f"**Enrichment spend:** {plan.enrichment_spent_cents}¢ / {plan.enrichment_budget_cents}¢ cap\n")
    lines.append("## TL;DR")
    lines.append(f"- {len(approvable)} new CRM account(s) ready to create on approval.")
    lines.append(f"- {sum(len(e.contacts_to_create) for e in approvable)} contact proposal(s) attached.")
    lines.append(f"- {len(blocked_policy)} entries blocked by policy - see below.")
    lines.append(f"- {len(skipped_existing) + len(skipped_ambiguous)} accounts skipped (already in CRM or ambiguous).\n")

    if approvable:
        lines.append("## Approvable entries\n")
        for e in approvable:
            acc = accounts.get(e.account_id)
            dec = decisions.get(e.account_id)
            lines.append(f"### {acc.name if acc else e.account_id} (`{acc.domain if acc else '?'}`)")
            if acc:
                lines.append(f"- Industry: {acc.industry or 'unknown'} · Employees: {acc.employees or 'unknown'} "
                             f"· HQ: {acc.hq_country or '?'}/{acc.hq_state or '?'}")
            if dec:
                lines.append(f"- Decision: **{dec.decision}** (confidence {dec.confidence:.2f}, by {dec.decided_by})")
                lines.append(f"- Evidence: {'; '.join(dec.evidence) or '-'}")
                if dec.missing:
                    lines.append(f"- Missing: {'; '.join(dec.missing)}")
            if e.contacts_to_create:
                lines.append("- Contacts to create:")
                for c in e.contacts_to_create:
                    verif = "✓" if c.verified else "?"
                    lines.append(f"  - {verif} {c.first_name or ''} {c.last_name or ''}"
                                 f" ({c.persona}) · email: {c.email or '-'}")
            else:
                why = {
                    "budget_exceeded": "enrichment skipped: budget cap reached",
                    "provider_failed": "enrichment provider failed",
                    "done": "provider returned no matching contact",
                    "not_needed": "no enrichment requested",
                }[e.enrichment_status]
                lines.append(f"- Contacts to create: *(none - {why})*")
            lines.append("")

    if blocked_policy:
        lines.append("## Policy-blocked entries\n")
        for e in blocked_policy:
            acc = accounts.get(e.account_id)
            lines.append(f"- **{acc.name if acc else e.account_id}** - {', '.join(e.policy_failures)}")
        lines.append("")

    if skipped_existing:
        lines.append("## Skipped - already in CRM\n")
        for e in skipped_existing:
            acc = accounts.get(e.account_id)
            lines.append(f"- {acc.name if acc else e.account_id} → CRM `{e.crm_account_id}`")
        lines.append("")

    if skipped_ambiguous:
        lines.append("## Skipped - ambiguous CRM match (needs human review)\n")
        for e in skipped_ambiguous:
            acc = accounts.get(e.account_id)
            lines.append(f"- {acc.name if acc else e.account_id}: {e.skip_reason}")
        lines.append("")

    lines.append("---")
    lines.append("To approve:  `gtm-lab approve " + plan.run_id + "`")
    lines.append("To reject:   `gtm-lab reject "  + plan.run_id + " --reason '...'`")
    lines.append("To inspect:  `gtm-lab inspect " + plan.run_id + "`")
    return "\n".join(lines) + "\n"
