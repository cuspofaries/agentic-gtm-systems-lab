"""SQLite-backed mock CRM.

State persists across process restarts (file-backed DB). Idempotency keys use `external_ref`:
retrying the same write returns the previously-created record instead of creating a duplicate.

Fault injection: the `fail_next` list can be primed in tests to make the next N writes raise
a retriable CRMError. Nothing in production code touches this attribute.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from ..models import ContactProposal, CRMAccount, CRMContact
from ..util import canonicalize_domain
from .base import CRMAdapter, CRMError


class MockCRM(CRMAdapter):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS crm_account (
        crm_account_id TEXT PRIMARY KEY,
        domain TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        source TEXT NOT NULL,
        external_ref TEXT UNIQUE
    );
    CREATE TABLE IF NOT EXISTS crm_contact (
        crm_contact_id TEXT PRIMARY KEY,
        crm_account_id TEXT NOT NULL,
        email TEXT,
        first_name TEXT,
        last_name TEXT,
        title TEXT,
        updated_at TEXT NOT NULL,
        external_ref TEXT UNIQUE,
        FOREIGN KEY (crm_account_id) REFERENCES crm_account(crm_account_id)
    );
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fail_next: deque[str] = deque()  # queue of error messages to inject
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    # --- test-only fault injection ---

    def inject_failure(self, message: str = "Transient CRM error", *, times: int = 1) -> None:
        for _ in range(times):
            self._fail_next.append(message)

    # --- CRMAdapter implementation ---

    def find_accounts_by_domain(self, domain: str) -> list[CRMAccount]:
        d = canonicalize_domain(domain)
        with self._conn() as c:
            rows = c.execute(
                "SELECT crm_account_id, domain, name, updated_at, source FROM crm_account WHERE domain = ?",
                (d,),
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def find_contacts_by_account(self, crm_account_id: str) -> list[CRMContact]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT crm_contact_id, crm_account_id, email, first_name, last_name, title, updated_at "
                "FROM crm_contact WHERE crm_account_id = ?",
                (crm_account_id,),
            ).fetchall()
        return [self._row_to_contact(r) for r in rows]

    def find_account_by_external_ref(self, external_ref: str) -> CRMAccount | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT crm_account_id, domain, name, updated_at, source FROM crm_account WHERE external_ref = ?",
                (external_ref,),
            ).fetchone()
        return self._row_to_account(row) if row else None

    def find_contact_by_external_ref(self, external_ref: str) -> CRMContact | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT crm_contact_id, crm_account_id, email, first_name, last_name, title, updated_at "
                "FROM crm_contact WHERE external_ref = ?",
                (external_ref,),
            ).fetchone()
        return self._row_to_contact(row) if row else None

    def create_account(
        self, *, domain: str, name: str, source: str, external_ref: str
    ) -> CRMAccount:
        self._maybe_fail()
        d = canonicalize_domain(domain)
        with self._conn() as c:
            # Idempotence on external_ref
            existing = c.execute(
                "SELECT crm_account_id, domain, name, updated_at, source FROM crm_account WHERE external_ref = ?",
                (external_ref,),
            ).fetchone()
            if existing:
                return self._row_to_account(existing)
            # Prevent duplicate domain writes (another source wrote it first)
            dup = c.execute(
                "SELECT crm_account_id FROM crm_account WHERE domain = ?", (d,)
            ).fetchone()
            if dup:
                raise CRMError(
                    f"Domain {d} already owned by {dup[0]} (written by another source)",
                    retriable=False,
                )
            acc_id = f"acc_{_short_hash(external_ref)}"
            now = datetime.now(UTC).isoformat()
            c.execute(
                "INSERT INTO crm_account (crm_account_id, domain, name, updated_at, source, external_ref) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (acc_id, d, name, now, source, external_ref),
            )
            return CRMAccount(
                crm_account_id=acc_id,
                domain=d,
                name=name,
                updated_at=datetime.fromisoformat(now),
                source=source,
            )

    def create_contact(
        self, proposal: ContactProposal, *, crm_account_id: str, external_ref: str
    ) -> CRMContact:
        self._maybe_fail()
        with self._conn() as c:
            existing = c.execute(
                "SELECT crm_contact_id, crm_account_id, email, first_name, last_name, title, updated_at "
                "FROM crm_contact WHERE external_ref = ?",
                (external_ref,),
            ).fetchone()
            if existing:
                return self._row_to_contact(existing)
            parent = c.execute(
                "SELECT crm_account_id FROM crm_account WHERE crm_account_id = ?",
                (crm_account_id,),
            ).fetchone()
            if not parent:
                raise CRMError(
                    f"No parent account {crm_account_id} for contact external_ref={external_ref}",
                    retriable=False,
                )
            now = datetime.now(UTC).isoformat()
            cid = f"con_{_short_hash(external_ref)}"
            c.execute(
                "INSERT INTO crm_contact "
                "(crm_contact_id, crm_account_id, email, first_name, last_name, title, updated_at, external_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    crm_account_id,
                    proposal.email,
                    proposal.first_name,
                    proposal.last_name,
                    proposal.title,
                    now,
                    external_ref,
                ),
            )
            return CRMContact(
                crm_contact_id=cid,
                crm_account_id=crm_account_id,
                email=proposal.email,
                first_name=proposal.first_name,
                last_name=proposal.last_name,
                title=proposal.title,
                updated_at=datetime.fromisoformat(now),
            )

    def all_accounts(self) -> list[CRMAccount]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT crm_account_id, domain, name, updated_at, source FROM crm_account ORDER BY updated_at"
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    # --- helpers ---

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA foreign_keys = ON")
        return c

    def _maybe_fail(self) -> None:
        if self._fail_next:
            msg = self._fail_next.popleft()
            raise CRMError(msg, retriable=True)

    @staticmethod
    def _row_to_account(r: tuple) -> CRMAccount:
        return CRMAccount(
            crm_account_id=r[0],
            domain=r[1],
            name=r[2],
            updated_at=datetime.fromisoformat(r[3]),
            source=r[4],
        )

    @staticmethod
    def _row_to_contact(r: tuple) -> CRMContact:
        return CRMContact(
            crm_contact_id=r[0],
            crm_account_id=r[1],
            email=r[2],
            first_name=r[3],
            last_name=r[4],
            title=r[5],
            updated_at=datetime.fromisoformat(r[6]),
        )


def _short_hash(s: str) -> str:
    import hashlib

    return hashlib.sha1(s.encode()).hexdigest()[:10]
