# ADR 0001: Pydantic for data contracts

## Status

Accepted · 2026-11-21

## Context

Every stage of the workflow exchanges structured data: accounts, qualification decisions, write plans, provider results. Options considered:

1. Plain `dict` with runtime assertions.
2. `dataclasses.dataclass` with manual validation.
3. Pydantic v2 models.
4. `attrs` + `cattrs` + explicit converters.

## Decision

Pydantic v2 across the board (`src/gtm_lab/models.py`).

## Consequences

- Every model gets automatic JSON (de)serialization for free - valuable for the SQLite store and the event log.
- `extra="forbid"` catches drift in fixtures: an unexpected YAML key fails loud.
- `field_validator` carries the domain canonicalization into the model, so every consumer reads the same normalized form.
- Pydantic is a hard runtime dependency. Acceptable given the brief lists it as the preferred stack.
- Nothing stops us from migrating one model at a time if we ever need to shed the dep - the orchestrator only cares about duck-typed attributes.
