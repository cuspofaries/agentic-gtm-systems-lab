# ADR 0002: SQLite for V1, Postgres compatibility later

## Status

Accepted · 2026-11-21

## Context

The brief says "SQLite for local persistence" with "PostgreSQL compatibility as a future extension". V1 state fits in one file per workflow.

## Decision

SQLite 3 via the Python standard library. Two separate DB files per lab:

- `runs.sqlite` - per-run state (accounts, decisions, matches, costs, plan, write results)
- `crm.sqlite` - mock CRM state

Both live under `.gtm-lab-state/` by default; override with `GTM_LAB_STATE_DIR`.

## Consequences

- Zero external services for the demo. `pip install` + one command.
- No SQL abstraction library - raw cursor queries. This is fine at 6 tables; revisit if the schema grows.
- Migration to Postgres later means replacing the two `_conn()` methods and swapping `TEXT`/`JSON` column types; the orchestrator code is untouched.
- Concurrent writers would corrupt SQLite without WAL mode. V1 assumes single-writer; multi-writer is out of scope.
- Testing is trivial: every test gets its own temp DB via the `tmp_path` fixture.
