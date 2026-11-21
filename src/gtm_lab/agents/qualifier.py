"""ICP qualification.

Two implementations:

- `DeterministicQualifier` is the offline-mode baseline. Pure Python rules, no LLM.
  It is a reasonable baseline: it fires on every "clear yes" and "clear no", and emits
  `insufficient_evidence` for the genuinely ambiguous cases rather than guessing.

- `LLMQualifier` is the optional agentic path. It is NOT called by the offline demo.
  The brief says: reserve LLM reasoning for ambiguous, contextual decisions that
  genuinely benefit from it. We only hand the LLM an account that the deterministic
  qualifier already marked `insufficient_evidence`.

Both qualifiers return a fully structured `QualificationDecision`.
"""

from __future__ import annotations

from typing import Protocol

from ..context import GTMContext
from ..models import Account, Confidence, QualificationDecision
from ..util import within_window


class Qualifier(Protocol):
    def qualify(self, account: Account, ctx: GTMContext) -> QualificationDecision: ...


class DeterministicQualifier:
    """Rule-based ICP qualifier. No external calls, fully reproducible."""

    name = "rules"

    def qualify(self, account: Account, ctx: GTMContext) -> QualificationDecision:
        evidence: list[str] = []
        missing: list[str] = []
        disqualifiers: list[str] = []

        # --- Exclusions take precedence ---
        for rule in ctx.exclusions:
            industry = (account.industry or "").lower()
            if rule.industry_keywords and any(k.lower() in industry for k in rule.industry_keywords):
                disqualifiers.append(f"exclusion[{rule.name}]: {rule.reason}")
            if rule.domain_suffixes and any(
                account.domain.endswith(s.lower()) for s in rule.domain_suffixes
            ):
                disqualifiers.append(f"exclusion[{rule.name}]: domain suffix")

        if disqualifiers:
            return QualificationDecision(
                account_id=account.account_id,
                decision="not_qualified",
                explanation="Hit an exclusion rule.",
                evidence=disqualifiers,
                missing=missing,
                confidence=1.0,
                decided_by=self.name,
            )

        # --- Geography ---
        geo_ok = True
        if ctx.icp.geographies:
            if account.hq_country is None:
                missing.append("hq_country unknown")
                geo_ok = False
            elif account.hq_country.upper() not in {g.upper() for g in ctx.icp.geographies}:
                disqualifiers.append(f"hq_country {account.hq_country} outside {ctx.icp.geographies}")
            else:
                evidence.append(f"hq_country {account.hq_country} in-scope")

        # --- Employee count ---
        emp_ok = True
        if account.employees is None:
            missing.append("employees unknown")
            emp_ok = False
        elif not (ctx.icp.employees_min <= account.employees <= ctx.icp.employees_max):
            disqualifiers.append(
                f"employees {account.employees} outside [{ctx.icp.employees_min}, {ctx.icp.employees_max}]"
            )
        else:
            evidence.append(
                f"employees {account.employees} in [{ctx.icp.employees_min}, {ctx.icp.employees_max}]"
            )

        # Confidence adjustment: APPROX or UNKNOWN headcount lowers evidence weight
        emp_conf_penalty = 0.0
        if account.employees is not None and account.employees_confidence in {
            Confidence.APPROX,
            Confidence.UNKNOWN,
        }:
            emp_conf_penalty = 0.15

        # --- Industry ---
        industry_ok = True
        if ctx.icp.industry_keywords:
            industry = (account.industry or "").lower()
            if not industry:
                missing.append("industry unknown")
                industry_ok = False
            elif not any(k.lower() in industry for k in ctx.icp.industry_keywords):
                disqualifiers.append(f"industry '{account.industry}' not in {ctx.icp.industry_keywords}")
            else:
                evidence.append(f"industry matches '{account.industry}'")

        # --- Signals: optional booster; strict mode makes them required ---
        fresh_signals = [
            s
            for s in account.signals
            if s.kind in ctx.signal_policy.relevant_kinds
            and within_window(s.event_date, ctx.signal_policy.max_signal_age_days)
        ]
        if fresh_signals:
            evidence.append(f"{len(fresh_signals)} relevant signal(s) within window")
        elif ctx.policy.require_signal_for_qualification:
            disqualifiers.append("no relevant signal in freshness window")

        # --- Disqualifiers short-circuit ---
        if disqualifiers:
            return QualificationDecision(
                account_id=account.account_id,
                decision="not_qualified",
                explanation="Failed one or more ICP criteria.",
                evidence=disqualifiers,
                missing=missing,
                confidence=1.0,
                decided_by=self.name,
            )

        # --- Missing critical fields → insufficient_evidence ---
        if not (geo_ok and emp_ok and industry_ok):
            return QualificationDecision(
                account_id=account.account_id,
                decision="insufficient_evidence",
                explanation="Not enough data to decide one way or the other.",
                evidence=evidence,
                missing=missing,
                confidence=0.4,
                decided_by=self.name,
            )

        # --- Qualified ---
        base_conf = 0.7 + 0.1 * (1 if fresh_signals else 0)
        base_conf -= emp_conf_penalty
        base_conf = max(0.3, min(0.99, base_conf))

        if len(evidence) < ctx.policy.min_evidence_count:
            # Policy requires more evidence than we gathered
            return QualificationDecision(
                account_id=account.account_id,
                decision="insufficient_evidence",
                explanation=(
                    f"Account looks in-ICP but has only {len(evidence)} evidence facts "
                    f"(policy requires {ctx.policy.min_evidence_count})."
                ),
                evidence=evidence,
                missing=missing,
                confidence=0.5,
                decided_by=self.name,
            )

        return QualificationDecision(
            account_id=account.account_id,
            decision="qualified",
            explanation="All ICP criteria matched with sufficient evidence.",
            evidence=evidence,
            missing=missing,
            confidence=base_conf,
            decided_by=self.name,
        )


class LLMQualifier:
    """LLM-powered qualifier for ambiguous cases ONLY.

    `provider_call` does the model call (see `anthropic_provider.AnthropicProviderCall`).
    Without one, the qualifier returns `insufficient_evidence` with an explicit note.
    Whatever the provider returns is treated as untrusted and validated here.
    """

    name = "llm"

    def __init__(self, provider_call=None):
        self.provider_call = provider_call  # Callable[[str, dict], dict] or None

    def qualify(self, account: Account, ctx: GTMContext) -> QualificationDecision:
        if self.provider_call is None:
            return QualificationDecision(
                account_id=account.account_id,
                decision="insufficient_evidence",
                explanation="LLM qualifier was invoked but no provider is configured. "
                "Set ANTHROPIC_API_KEY and wire a provider_call to enable.",
                evidence=[],
                missing=["llm_provider_unconfigured"],
                confidence=0.0,
                decided_by=self.name,
            )
        # A real implementation would hand the LLM the serialized context + account and
        # parse a structured decision. Treat its output as untrusted - reject if the
        # schema check fails or evidence is empty.
        raw = self.provider_call(
            account.model_dump_json(),
            ctx.model_dump(),
        )
        decision = raw.get("decision")
        if decision not in {"qualified", "not_qualified", "insufficient_evidence"}:
            return QualificationDecision(
                account_id=account.account_id,
                decision="insufficient_evidence",
                explanation="LLM output rejected: unknown decision value.",
                evidence=[],
                missing=["llm_output_invalid"],
                confidence=0.0,
                decided_by=self.name,
            )
        evidence = [str(x) for x in raw.get("evidence", [])]
        if decision == "qualified" and not evidence:
            return QualificationDecision(
                account_id=account.account_id,
                decision="insufficient_evidence",
                explanation="LLM said 'qualified' with no evidence - rejected.",
                evidence=[],
                missing=["llm_evidence_absent"],
                confidence=0.0,
                decided_by=self.name,
            )
        return QualificationDecision(
            account_id=account.account_id,
            decision=decision,
            explanation=str(raw.get("explanation", ""))[:1000],
            evidence=evidence[:20],
            missing=[str(x) for x in raw.get("missing", [])][:20],
            confidence=float(raw.get("confidence", 0.5)),
            decided_by=self.name,
        )
