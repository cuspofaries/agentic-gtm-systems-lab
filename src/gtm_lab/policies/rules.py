"""Hard policy checks. Independent of the LLM.

The rule is simple: if `evaluate_entry` returns a non-empty list, the entry MUST NOT be
promoted to approved. The human approver is shown the list of policy failures.

Adding a new policy means: one more function here + one more test in
`tests/scenarios/test_scenarios.py`.
"""

from __future__ import annotations

from ..context import GTMContext
from ..models import Account, QualificationDecision, WritePlanEntry


def evaluate_entry(
    *,
    account: Account,
    decision: QualificationDecision,
    entry: WritePlanEntry,
    context: GTMContext,
) -> list[str]:
    """Return a list of human-readable policy failures. Empty list = passes."""
    failures: list[str] = []

    # 1. Decision must be `qualified` (not `insufficient_evidence` or `not_qualified`)
    if decision.decision != "qualified":
        failures.append(f"Decision is {decision.decision!r}, not 'qualified'")

    # 2. Evidence floor
    if len(decision.evidence) < context.policy.min_evidence_count:
        failures.append(
            f"Only {len(decision.evidence)} evidence facts "
            f"(policy minimum is {context.policy.min_evidence_count})"
        )

    # 3. Contact-with-email requirement
    if context.policy.require_contact_email_for_create and entry.crm_action == "create_account":
        has_email_contact = any(c.email for c in entry.contacts_to_create)
        if not has_email_contact:
            failures.append(
                "Policy requires at least one contact with an email for new-account creation"
            )

    # 4. Agent-asserted claims that cannot be grounded in Account facts
    # The brief says: "Unsupported agent claims" must not silently become approved.
    # We implement this by refusing an entry whose decision cites evidence that doesn't
    # correspond to an observable field on the Account.
    observable = {
        "hq_country",
        "hq_state",
        "industry",
        "employees",
        "signal",
        "signals",
        "domain",
    }
    unsupported = [
        e
        for e in decision.evidence
        if not any(key in e.lower() for key in observable)
    ]
    if unsupported:
        failures.append(
            f"{len(unsupported)} evidence fact(s) don't ground in observable Account fields: "
            f"{unsupported[:3]}"
        )

    # 5. Fields_missing cross-check: a decision that cites a field as evidence while
    # the account explicitly reports it as missing is a bug somewhere upstream, unless
    # research filled that field from checked, sourced claims (typed, not string-matched).
    filled = {r.field for r in decision.resolutions if r.status == "filled"}
    for fm in account.fields_missing:
        if fm in filled:
            continue
        if any(fm.lower() in e.lower() for e in decision.evidence):
            failures.append(f"Evidence references field '{fm}' but Account flags it missing")

    # 6. A field filled by research must rest on at least one claim that passed its checks
    # (quote in a page read in the run, page about this company).
    checked = {c.claim_id for c in decision.claims if c.check == "quote_found"}
    for r in decision.resolutions:
        if r.status == "filled" and not (set(r.claim_ids) & checked):
            failures.append(f"Field '{r.field}' was filled by research without a checked source")

    return failures
