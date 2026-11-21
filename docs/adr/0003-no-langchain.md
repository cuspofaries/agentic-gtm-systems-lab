# ADR 0003: No LangChain / LangGraph / Temporal / Celery / Redis / Kubernetes

## Status

Accepted · 2026-11-21

## Context

The brief says: "Avoid introducing LangChain, LangGraph, Temporal, Celery, Redis, Kubernetes, or additional services unless you can demonstrate a concrete requirement that cannot be met cleanly without them."

## Decision

None of them in V1. Specifically:

- **LangChain / LangGraph**: the only agentic step is the optional qualifier. One LLM call does not need a graph framework.
- **Temporal / Celery**: workflow state fits in SQLite; `gtm-lab sync` is one-shot. No distributed workers required.
- **Redis**: nothing to cache. The one piece of mutable state is the plan, which belongs in persistent storage, not a cache.
- **Kubernetes / Docker Compose**: the whole thing runs as `pip install -e .` + CLI calls.

## Consequences

- The repo is readable end to end in ~2000 lines of Python.
- Onboarding a new contributor is a `pip install` away.
- When a real user hits a limit (say, needs scheduled runs across machines), they can choose: wrap the CLI in cron, add a thin FastAPI layer, or adopt a real orchestrator. We don't pre-decide for them.
- An ADR to revisit this decision is not required - a future PR that adds any of these libraries is itself the decision point.
