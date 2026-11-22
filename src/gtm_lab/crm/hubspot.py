"""HubSpot implementation of `CRMAdapter`.

Behavior measured on a HubSpot test portal (API version 2026-09), not assumed:

- HubSpot accepts two companies with the same domain. Domain uniqueness is enforced here,
  by searching before every create.
- HubSpot stores the domain exactly as typed ("www.acquia.com", "https://hyland.com/").
  The domain search therefore asks for every common spelling (scheme, www, trailing slash)
  with the IN operator; the comparison itself is case-insensitive.
- The search index lags by a few seconds (about 6.5 s measured). A company created by
  someone else in that window can be missed by the domain check. The external-ref lookup
  is a direct read and does not lag.
- Idempotency uses a custom property `gtm_lab_external_ref` with `hasUniqueValue: true`,
  created only by `ensure_schema()` (the explicit `gtm-lab crm-setup` command). HubSpot
  then rejects a second record with the same value (400 VALIDATION_ERROR), so a retried
  write of the same run can never create a duplicate. Two different runs use different
  refs; between them only the domain check (and its lag) applies.
- Portals with GDPR enabled require `hs_legal_basis` on contacts. It comes from
  `crm.hubspot_contact_legal_basis` in the GTM context; there is no default.
- Emails on `.invalid` are rejected by HubSpot.

Standard library only (urllib), so the package gains no dependency.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from ..models import ContactProposal, CRMAccount, CRMContact
from ..util import canonicalize_domain
from .base import CRMAdapter, CRMError

API_VERSION = "2026-09"
REF_PROPERTY = "gtm_lab_external_ref"
CONTACT_TO_COMPANY = 279  # HUBSPOT_DEFINED, unlabeled contact -> company
PROPERTY_GROUPS = {"companies": "companyinformation", "contacts": "contactinformation"}

# (method, url, headers, body bytes or None) -> (status, parsed JSON or raw text, headers)
Transport = Callable[[str, str, dict, bytes | None], tuple[int, object, dict]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, _parse(raw), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read()), dict(e.headers or {})


def _parse(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")[:500]


class HubSpotCRM(CRMAdapter):
    name = "hubspot"

    def __init__(
        self,
        token: str,
        *,
        contact_legal_basis: str | None = None,
        base_url: str = "https://api.hubapi.com",
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not token:
            raise ValueError("HubSpot token is empty")
        self._token = token
        self.contact_legal_basis = contact_legal_basis
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self._schema_ready = False
        self._identity: str | None = None

    # --- HTTP -----------------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None, *, ok404: bool = False):
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        try:
            status, payload, resp_headers = self._transport(
                method, self.base_url + path, headers, data
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CRMError(f"HubSpot unreachable: {type(e).__name__}", retriable=True) from e
        if status < 300:
            return payload
        if status == 404 and ok404:
            return None
        message = _message(payload)
        if status == 429:
            retry_after = _float(resp_headers.get("Retry-After") or resp_headers.get("retry-after"))
            self._sleep(min(retry_after or 1.0, 10.0))
            raise CRMError(f"HubSpot rate limit (429): {message}", retriable=True)
        if status >= 500:
            raise CRMError(f"HubSpot server error ({status}): {message}", retriable=True)
        raise _ClientError(status, message)

    def _objects(self, obj: str) -> str:
        return f"/crm/objects/{API_VERSION}/{obj}"

    # --- identity and schema ----------------------------------------------------

    def identity(self) -> str:
        """`hubspot:<portalId>`, read from the account the token belongs to."""
        if self._identity is None:
            info = self._call("GET", "/account-info/v3/details")
            self._identity = f"hubspot:{info['portalId']}"
        return self._identity

    def missing_schema(self) -> list[str]:
        """Objects lacking the unique external-ref property. Read-only."""
        missing = []
        for obj in PROPERTY_GROUPS:
            path = f"/crm/properties/{API_VERSION}/{obj}/{REF_PROPERTY}"
            if self._call("GET", path, ok404=True) is None:
                missing.append(obj)
        return missing

    def check_ready(self) -> None:
        if self._schema_ready:
            return
        missing = self.missing_schema()
        if missing:
            raise CRMError(
                f"HubSpot portal {self.identity()} has no '{REF_PROPERTY}' property on "
                f"{', '.join(missing)}. Run `gtm-lab crm-setup --crm hubspot` first.",
                retriable=False,
            )
        self._schema_ready = True

    def ensure_schema(self) -> list[str]:
        """Create the unique external-ref property where missing; return what was created.

        This changes the portal's schema and `hasUniqueValue` cannot be undone, so it is only
        called by the explicit `gtm-lab crm-setup` command, never by planning or syncing.
        """
        created = []
        for obj, group in PROPERTY_GROUPS.items():
            path = f"/crm/properties/{API_VERSION}/{obj}"
            if self._call("GET", f"{path}/{REF_PROPERTY}", ok404=True) is None:
                created.append(obj)
                self._call(
                    "POST",
                    path,
                    {
                        "name": REF_PROPERTY,
                        "label": "gtm-lab external ref",
                        "type": "string",
                        "fieldType": "text",
                        "groupName": group,
                        "hasUniqueValue": True,
                        "description": "Idempotency key written by agentic-gtm-systems-lab.",
                    },
                )
        self._schema_ready = True
        return created

    # --- reads ----------------------------------------------------------------

    def find_accounts_by_domain(self, domain: str) -> list[CRMAccount]:
        d = canonicalize_domain(domain)
        payload = self._call(
            "POST",
            f"{self._objects('companies')}/search",
            {
                "filterGroups": [{"filters": [
                    {"propertyName": "domain", "operator": "IN", "values": domain_spellings(d)}
                ]}],
                "properties": ["domain", "name", "hs_lastmodifieddate", REF_PROPERTY],
                "limit": 10,
            },
        )
        return [self._to_account(r) for r in (payload or {}).get("results", [])]

    def find_account_by_external_ref(self, external_ref: str) -> CRMAccount | None:
        self.check_ready()
        r = self._call(
            "GET",
            f"{self._objects('companies')}/{_q(external_ref)}"
            f"?idProperty={REF_PROPERTY}&properties=domain,name,hs_lastmodifieddate,{REF_PROPERTY}",
            ok404=True,
        )
        return self._to_account(r) if r else None

    def find_contact_by_external_ref(self, external_ref: str) -> CRMContact | None:
        self.check_ready()
        r = self._call(
            "GET",
            f"{self._objects('contacts')}/{_q(external_ref)}"
            f"?idProperty={REF_PROPERTY}&properties=firstname,lastname,email,jobtitle,lastmodifieddate"
            "&associations=companies",
            ok404=True,
        )
        return self._to_contact(r) if r else None

    def find_contacts_by_account(self, crm_account_id: str) -> list[CRMContact]:
        payload = self._call(
            "GET", f"/crm/v4/objects/companies/{_q(crm_account_id)}/associations/contacts"
        )
        out = []
        for row in (payload or {}).get("results", []):
            r = self._call(
                "GET",
                f"{self._objects('contacts')}/{row['toObjectId']}"
                "?properties=firstname,lastname,email,jobtitle,lastmodifieddate",
                ok404=True,
            )
            if r:
                out.append(self._to_contact(r, crm_account_id=crm_account_id))
        return out

    def all_accounts(self) -> list[CRMAccount]:
        out, after = [], None
        while len(out) < 1000:
            path = f"{self._objects('companies')}?limit=100&properties=domain,name,hs_lastmodifieddate"
            payload = self._call("GET", path + (f"&after={after}" if after else ""))
            out += [self._to_account(r) for r in payload.get("results", [])]
            after = ((payload.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break
        return out

    # --- writes ---------------------------------------------------------------

    def create_account(
        self, *, domain: str, name: str, source: str, external_ref: str
    ) -> CRMAccount:
        self.check_ready()
        existing = self.find_account_by_external_ref(external_ref)
        if existing:
            return existing
        d = canonicalize_domain(domain)
        owners = self.find_accounts_by_domain(d)
        if owners:
            raise CRMError(
                f"Domain {d} already owned by HubSpot company {owners[0].crm_account_id}",
                retriable=False,
            )
        try:
            r = self._call(
                "POST",
                self._objects("companies"),
                {"properties": {"name": name, "domain": d, REF_PROPERTY: external_ref}},
            )
        except _ClientError as e:
            if _is_ref_conflict(e.message):
                # A concurrent retry won the race: the unique property protected us.
                found = self.find_account_by_external_ref(external_ref)
                if found:
                    return found
            raise CRMError(f"HubSpot rejected the company ({e.status}): {e.message}") from e
        return self._to_account(r)

    def create_contact(
        self, proposal: ContactProposal, *, crm_account_id: str, external_ref: str
    ) -> CRMContact:
        self.check_ready()
        existing = self.find_contact_by_external_ref(external_ref)
        if existing:
            return existing
        props = {
            "firstname": proposal.first_name,
            "lastname": proposal.last_name,
            "jobtitle": proposal.title,
            "email": proposal.email,
            REF_PROPERTY: external_ref,
        }
        if self.contact_legal_basis:
            props["hs_legal_basis"] = self.contact_legal_basis
        props = {k: v for k, v in props.items() if v is not None}
        body = {
            "properties": props,
            "associations": [
                {
                    "to": {"id": crm_account_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": CONTACT_TO_COMPANY}
                    ],
                }
            ],
        }
        try:
            r = self._call("POST", self._objects("contacts"), body)
        except _ClientError as e:
            if _is_ref_conflict(e.message):
                found = self.find_contact_by_external_ref(external_ref)
                if found:
                    return found
            hint = ""
            if "hs_legal_basis" in e.message:
                hint = " Set crm.hubspot_contact_legal_basis in gtm-context.yaml (see docs/configuration.md)."
            raise CRMError(f"HubSpot rejected the contact ({e.status}): {e.message}{hint}") from e
        return self._to_contact(r, crm_account_id=crm_account_id)

    # --- mapping --------------------------------------------------------------

    @staticmethod
    def _to_account(r: dict) -> CRMAccount:
        p = r.get("properties", {})
        return CRMAccount(
            crm_account_id=str(r["id"]),
            domain=p.get("domain") or "",
            name=p.get("name") or "",
            updated_at=_dt(p.get("hs_lastmodifieddate") or r.get("updatedAt")),
            source=p.get(REF_PROPERTY) or "hubspot",
        )

    @staticmethod
    def _to_contact(r: dict, *, crm_account_id: str | None = None) -> CRMContact:
        p = r.get("properties", {})
        if crm_account_id is None:
            assoc = ((r.get("associations") or {}).get("companies") or {}).get("results") or []
            crm_account_id = str(assoc[0]["id"]) if assoc else ""
        return CRMContact(
            crm_contact_id=str(r["id"]),
            crm_account_id=crm_account_id,
            email=p.get("email"),
            first_name=p.get("firstname"),
            last_name=p.get("lastname"),
            title=p.get("jobtitle"),
            updated_at=_dt(p.get("lastmodifieddate") or r.get("updatedAt")),
        )


def domain_spellings(domain: str) -> list[str]:
    """The ways people type a domain into a CRM field, all pointing at the same company."""
    return [
        f"{scheme}{www}{domain}{slash}"
        for scheme in ("", "http://", "https://")
        for www in ("", "www.")
        for slash in ("", "/")
    ]


class _ClientError(CRMError):
    """A 4xx from HubSpot: not retriable. Keeps status and message for conflict checks."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HubSpot rejected the request ({status}): {message}", retriable=False)
        self.status = status
        self.message = message


def _message(payload) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("category") or payload)[:400]
    return str(payload)[:200]


def _is_ref_conflict(message: str) -> bool:
    return f"propertyName={REF_PROPERTY}" in message


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dt(v) -> datetime:
    if not v:
        return datetime.now(UTC)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
