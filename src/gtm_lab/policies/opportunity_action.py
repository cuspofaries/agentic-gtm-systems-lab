"""Opportunity action policy: from a checked assessment to a proposed action. Code, not an LLM.

Used by `--enrichment-strategy opportunity_gated` to decide whether paid contact
enrichment is proposed for a new, ICP-qualified account, and for which personas. It
fails closed: anything other than a clean, evidence-backed assessment with a complete
persona mapping means no provider call. It never approves, writes or contacts anything;
an enrichment it proposes still goes through budgets, policies and human approval.

| Assessment | Action | Contact provider |
|---|---|---|
| `evidence_backed`, coverage `found`, no dropped claim, roles mapped to personas | `propose_enrichment` | called, mapped personas only |
| `not_relevant` | `defer` | not called |
| `needs_research`, `hypothesis_only` | `research_more` | not called |
| `insufficient_evidence` | `review` (or `research_more` if coverage was incomplete or failed) | not called |
| no agent assessment (cap reached, not selected, baseline only) | `review` | not called |
| dropped claims, incomplete persona mapping | `review` | not called |

"Not assessed" never means "not relevant".
"""

from __future__ import annotations

from ..context import GTMContext
from ..models import OpportunityAssessment, QualificationDecision


def decide(decision: QualificationDecision, assessment: OpportunityAssessment | None,
           ctx: GTMContext) -> tuple[str, str, list[str], dict[str, str]]:
    """Returns (action, reason, personas, persona_reasons)."""
    if decision.decision != "qualified":
        return "review", f"ICP decision is {decision.decision}; opportunity evidence never promotes it.", [], {}
    if assessment is None or assessment.assessed_by != "agent":
        return ("review", "Not assessed by the investigator (per-run cap reached or not selected). "
                "This is not a finding that the account is not relevant.", [], {})
    st, cov = assessment.assessment_status, assessment.coverage
    if st == "not_relevant":
        return "defer", "Checked sources point away from the offer's pains.", [], {}
    if st in ("needs_research", "hypothesis_only"):
        return "research_more", f"Assessment is {st}: the problem is not established by checked sources.", [], {}
    if st == "insufficient_evidence":
        if cov in ("incomplete", "failed"):
            return "research_more", f"Research coverage was {cov}; nothing is concluded.", [], {}
        return "review", "Not enough evidence either way.", [], {}
    # evidence_backed
    if cov != "found":
        return "review", f"Evidence-backed but coverage is {cov}.", [], {}
    if assessment.unsupported_claims:
        return ("review", f"{assessment.unsupported_claims} claim(s) were dropped by the checks; a person should "
                "look before paid enrichment.", [], {})
    by_id = {r.id: r for r in ctx.buying_roles}
    personas: list[str] = []
    reasons: dict[str, str] = {}
    unmapped = []
    for role in assessment.buying_roles:
        cfg = by_id.get(role.role_id)
        if cfg is None or not cfg.personas:
            unmapped.append(role.role_id)
            continue
        for p in cfg.personas:
            if p not in personas:
                personas.append(p)
                reasons[p] = f"role {role.role_id} ({role.role}) for supported pain(s): {role.reason}"
    if unmapped or not personas:
        return ("review", "Buying roles without a persona mapping: " + (", ".join(unmapped) or "none found")
                + ". Add `personas` to those buying_roles; nothing is enriched by default.", [], {})
    for p in ctx.personas:
        reasons.setdefault(p.name, "not linked to a supported pain's buying role")
    return "propose_enrichment", "Evidence-backed opportunity with checked sources.", personas, reasons
