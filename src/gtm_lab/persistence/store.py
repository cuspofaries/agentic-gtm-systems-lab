"""Workflow run state - persistent, append-only where it matters, survives restarts.

One SQLite file per lab. Schema:

- `account`        - raw discovered accounts per run
- `decision`       - qualification decisions per run
- `match`          - reconciliation matches per run
- `cost`           - enrichment costs per run
- `plan`           - the current WritePlan JSON per run (one row, updated in place)
- `write_result`   - outcomes of sync operations per run (append-only)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..models import (
    Account,
    EnrichmentCost,
    OpportunityAssessment,
    QualificationDecision,
    ReconciliationMatch,
    WriteOperationResult,
    WritePlan,
)


class RunStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS account (run_id TEXT, account_id TEXT, json TEXT,
        PRIMARY KEY (run_id, account_id));
    CREATE TABLE IF NOT EXISTS decision (run_id TEXT, account_id TEXT, json TEXT,
        PRIMARY KEY (run_id, account_id));
    CREATE TABLE IF NOT EXISTS match (run_id TEXT, account_id TEXT, json TEXT,
        PRIMARY KEY (run_id, account_id));
    CREATE TABLE IF NOT EXISTS cost (run_id TEXT, account_id TEXT, json TEXT,
        PRIMARY KEY (run_id, account_id));
    CREATE TABLE IF NOT EXISTS plan (run_id TEXT PRIMARY KEY, json TEXT);
    CREATE TABLE IF NOT EXISTS assessment (run_id TEXT, account_id TEXT, assessed_by TEXT, json TEXT,
        PRIMARY KEY (run_id, account_id, assessed_by));
    CREATE TABLE IF NOT EXISTS write_result (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, account_id TEXT, json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_write_result_run ON write_result(run_id);
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # --- save / load helpers ---

    def save_accounts(self, run_id: str, accounts: list[Account]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO account (run_id, account_id, json) VALUES (?, ?, ?)",
                [(run_id, a.account_id, a.model_dump_json()) for a in accounts],
            )

    def load_accounts(self, run_id: str) -> list[Account]:
        with self._conn() as c:
            rows = c.execute("SELECT json FROM account WHERE run_id = ?", (run_id,)).fetchall()
        return [Account.model_validate_json(r[0]) for r in rows]

    def save_decisions(self, run_id: str, decisions: list[QualificationDecision]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO decision (run_id, account_id, json) VALUES (?, ?, ?)",
                [(run_id, d.account_id, d.model_dump_json()) for d in decisions],
            )

    def load_decisions(self, run_id: str) -> list[QualificationDecision]:
        with self._conn() as c:
            rows = c.execute("SELECT json FROM decision WHERE run_id = ?", (run_id,)).fetchall()
        return [QualificationDecision.model_validate_json(r[0]) for r in rows]

    def save_matches(self, run_id: str, matches: list[ReconciliationMatch]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO match (run_id, account_id, json) VALUES (?, ?, ?)",
                [(run_id, m.account_id, m.model_dump_json()) for m in matches],
            )

    def load_matches(self, run_id: str) -> list[ReconciliationMatch]:
        with self._conn() as c:
            rows = c.execute("SELECT json FROM match WHERE run_id = ?", (run_id,)).fetchall()
        return [ReconciliationMatch.model_validate_json(r[0]) for r in rows]

    def load_costs(self, run_id: str) -> list[EnrichmentCost]:
        with self._conn() as c:
            rows = c.execute("SELECT json FROM cost WHERE run_id = ?", (run_id,)).fetchall()
        return [EnrichmentCost.model_validate_json(r[0]) for r in rows]

    def save_costs(self, run_id: str, costs: list[EnrichmentCost]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO cost (run_id, account_id, json) VALUES (?, ?, ?)",
                [(run_id, k.account_id, k.model_dump_json()) for k in costs],
            )

    def save_assessments(self, run_id: str, items: list[OpportunityAssessment]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO assessment (run_id, account_id, assessed_by, json) VALUES (?, ?, ?, ?)",
                [(run_id, a.account_id, a.assessed_by, a.model_dump_json()) for a in items],
            )

    def load_assessments(self, run_id: str) -> list[OpportunityAssessment]:
        with self._conn() as c:
            rows = c.execute("SELECT json FROM assessment WHERE run_id = ? ORDER BY account_id, assessed_by",
                             (run_id,)).fetchall()
        return [OpportunityAssessment.model_validate_json(r[0]) for r in rows]

    def save_plan(self, plan: WritePlan) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO plan (run_id, json) VALUES (?, ?)",
                (plan.run_id, plan.model_dump_json()),
            )

    def load_plan(self, run_id: str) -> WritePlan | None:
        with self._conn() as c:
            row = c.execute("SELECT json FROM plan WHERE run_id = ?", (run_id,)).fetchone()
        return WritePlan.model_validate_json(row[0]) if row else None

    def save_write_results(self, run_id: str, results: list[WriteOperationResult]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT INTO write_result (run_id, account_id, json) VALUES (?, ?, ?)",
                [(run_id, r.account_id, r.model_dump_json()) for r in results],
            )

    def load_write_results(self, run_id: str) -> list[WriteOperationResult]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT json FROM write_result WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [WriteOperationResult.model_validate_json(r[0]) for r in rows]

    def list_runs(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT run_id FROM plan ORDER BY run_id DESC").fetchall()
        return [r[0] for r in rows]

    # --- inspection ---

    def inspect(self, run_id: str) -> dict:
        """One-call snapshot for CLI inspect / debugging."""
        return {
            "run_id": run_id,
            "accounts": [json.loads(a.model_dump_json()) for a in self.load_accounts(run_id)],
            "decisions": [json.loads(d.model_dump_json()) for d in self.load_decisions(run_id)],
            "plan": (self.load_plan(run_id).model_dump() if self.load_plan(run_id) else None),
            "write_results": [
                json.loads(r.model_dump_json()) for r in self.load_write_results(run_id)
            ],
            "opportunity_assessments": [json.loads(a.model_dump_json()) for a in self.load_assessments(run_id)],
        }
