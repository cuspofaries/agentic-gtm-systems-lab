# Email finding and verification

Two optional steps run after contact enrichment, for each account:

- `--email-finder fullenrich`: finds the work email of contacts that have none (People Data Labs' free plan returns names and LinkedIn URLs, not emails).
- `--email-verifier zerobounce`: checks every email before it can reach the CRM.

Both default to `none`. They can be used together or alone, with any contact provider.

```bash
export FULLENRICH_API_KEY=...
export ZEROBOUNCE_API_KEY=...
gtm-lab demo --discovery pdl --enrichment pdl \
  --email-finder fullenrich --email-verifier zerobounce \
  --example-dir path/to/your-context-dir
```

## What a contact ends up with

Each provider's answer is normalized to one of four verdicts:

| Verdict | FullEnrich status | ZeroBounce status |
|---|---|---|
| `valid` | `DELIVERABLE` | `valid` |
| `risky` | `HIGH_PROBABILITY`, `CATCH_ALL` | `catch-all` |
| `invalid` | `INVALID`, `INVALID_DOMAIN` | `invalid`, `spamtrap`, `abuse`, `do_not_mail` |
| `unknown` | `UNVERIFIED` | `unknown` |

- When both run, the verifier has the last word: FullEnrich `DELIVERABLE` followed by ZeroBounce `catch-all` gives `risky`.
- `verified` is true only for `valid`.
- An `invalid` email is always removed before the plan.
- `risky` and `unknown` emails are kept with their verdict, unless the context says otherwise:

```yaml
contacts:
  on_unverified_email: drop_email   # default: keep
```

The review artifact shows each contact's email, its verdict and every check made, for example `riley@acme.com (risky) · checks: fullenrich:DELIVERABLE, zerobounce:catch-all`. A removed email shows as `email removed (invalid)` in the checks.

If the verifier is down or out of credits, the finder's verdict is kept and the failure is listed in the plan's source failures.

## Budget

Both steps are part of the enrichment budget. One account can cost at most: contact search, plus one find and one check per contact (`--profiles-per-account`). The workflow checks that maximum before calling and charges what was spent:

- FullEnrich charges one credit per email **found**, nothing when none is found (and nothing for a contact enriched again within 3 months).
- ZeroBounce charges one credit per check, none for `unknown`.

The budget is in cents, so a credit needs a value: `--email-finder-cents` (default `10`) and `--email-verifier-cents` (default `2`). These defaults are deliberately high round numbers, not published prices. Set them to what a credit costs on your plan.

## Provider behavior

### FullEnrich (API v2)

- `POST /contact/enrich/bulk` with first name, last name, company domain and name, the LinkedIn URL when known, and `enrich_fields: ["contact.work_emails"]` only (no phone). One request per account.
- Enrichment is asynchronous. The connector polls `GET /contact/enrich/bulk/{id}` (first after 10 s, then every 15 s, up to 5 minutes). Until it is done, FullEnrich answers 400 `error.enrichment.in_progress`.
- The email used is `most_probable_work_email`, which FullEnrich documents as the one with the lowest bounce rate, excluding invalid ones.
- `CREDITS_INSUFFICIENT`, `CANCELED` or a timeout are reported as failures; any email already found is kept.

### ZeroBounce (API v2)

- `POST /v2/validate`, form-encoded. The key travels in the body, never in a URL.
- **Measured:** a wrong key or an empty balance returns HTTP **200** with `{"error": "Invalid API key or your account ran out of credits"}`. The connector treats it as a failure, never as a verdict.
- ZeroBounce processes this endpoint in the United States.

## Testing without spending credits

Both providers have free test inputs, used to check the connectors live at no cost:

- ZeroBounce sandbox addresses such as `valid@example.com`, `catch_all@example.com`, `invalid@example.com`, `unknown@example.com` return the matching status and cost nothing.
- FullEnrich documents one test contact that costs nothing (see its credits documentation).

Both were run live this way: balances were the same before and after, statuses matched the table above, and a wrong ZeroBounce key was reported as a failure.

`tests/unit/test_email_pipeline.py` covers the same behavior with fake transports and runs in CI.

## Limits

- One finder and one verifier. Other providers (Hunter, NeverBounce, Apollo...) would implement `EmailFinder` or `EmailVerifier` in `providers/email.py`.
- FullEnrich is called once per account, so a run waits about 10 to 25 seconds per account that needs emails.
- A catch-all domain accepts every address: `risky` is the honest answer there, no provider can do better by SMTP.
