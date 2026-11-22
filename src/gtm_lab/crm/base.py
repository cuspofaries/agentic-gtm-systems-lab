"""CRM contract.

A real HubSpot/Salesforce adapter implements the same shape. Keep it minimal -
idempotence and reconciliation are the responsibility of the CRM implementation,
not of upstream workflow code. If a CRM can't do idempotent writes, the adapter wraps
them (e.g. by deduping on domain before POSTing a create).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ContactProposal, CRMAccount, CRMContact


class CRMError(Exception):
    """Raised for transient or permanent CRM failures. Carry the attempt count for retries."""

    def __init__(self, message: str, *, retriable: bool = False):
        super().__init__(message)
        self.retriable = retriable


class CRMAdapter(ABC):
    name: str = "crm"  # recorded on each plan; sync refuses a plan built against another CRM

    def identity(self) -> str:
        """Which CRM instance this adapter talks to (e.g. one HubSpot portal). Recorded on the
        plan and checked again before any write, so a changed token cannot redirect a sync."""
        return self.name

    def check_ready(self) -> None:  # noqa: B027 - optional hook, no-op for CRMs without setup
        """Raise CRMError(retriable=False) if the CRM is not prepared for writes.
        Read-only: preparing it is an explicit, separate step (`gtm-lab crm-setup`)."""

    @abstractmethod
    def find_accounts_by_domain(self, domain: str) -> list[CRMAccount]: ...

    @abstractmethod
    def find_contacts_by_account(self, crm_account_id: str) -> list[CRMContact]: ...

    @abstractmethod
    def find_account_by_external_ref(self, external_ref: str) -> CRMAccount | None:
        """Return the account this workflow previously wrote under `external_ref`, if any."""

    @abstractmethod
    def find_contact_by_external_ref(self, external_ref: str) -> CRMContact | None: ...

    @abstractmethod
    def create_account(
        self, *, domain: str, name: str, source: str, external_ref: str
    ) -> CRMAccount:
        """Create an account; idempotent on `external_ref`.

        Must raise a non-retriable CRMError if another record already owns `domain`:
        silently returning someone else's account would attach our contacts to it.
        """

    @abstractmethod
    def create_contact(
        self, proposal: ContactProposal, *, crm_account_id: str, external_ref: str
    ) -> CRMContact:
        """Create a contact attached to `crm_account_id`; idempotent on `external_ref`."""

    @abstractmethod
    def all_accounts(self) -> list[CRMAccount]: ...
