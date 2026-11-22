# People Data Labs providers

Two real providers replace the fixtures when you ask for them:

- `--discovery pdl`: finds companies with PDL's Company Search API.
- `--enrichment pdl`: finds people at each qualified company with PDL's Person Search API.

Both default to `fixture`. Combined with `--crm hubspot`, the whole workflow runs on real data: discovery, qualification, CRM reconciliation, contacts, approval, sync.

```bash
export PDL_API_KEY=...
export HUBSPOT_SERVICE_KEY=...        # test portal
gtm-lab demo --discovery pdl --enrichment pdl --crm hubspot \
  --limit 15 --profiles-per-account 2 --title-levels cxo,vp,director \
  --example-dir path/to/your-context-dir
```

`--example-dir` only needs `config/gtm-context.yaml` when both providers are PDL. Keep a directory with real data out of the repository.

## Context settings

Discovery always applies the ICP band (`icp.employees_min/max`, `icp.geographies`). Add PDL's own vocabulary for the kind of company:

```yaml
discovery:
  pdl_industries: ["computer software"]
  pdl_tags: ["software as a service"]
```

Person Search uses each persona's `titles_any_of`. A phrase matches when its words appear in that order in the job title, with anything in between: `vp sales` matches "vp, sales" and "rvp enterprise sales" but not "regional vice president of sales", so list `vice president of sales` as well.

## Credits and cost

- One credit per record returned. Company Search and Person Search have **separate quotas** on the free plan (100 per month each at the time of testing).
- The enrichment budget is in cents. A person credit is valued at PDL's published first paid tier, 28 cents, so the budget stops calls even on free credits. The budget is checked against the maximum a call can cost (`profiles-per-account` x 28) and charged what PDL reports spending (`x-call-credits-spent`).
- A search with no match is a 404 and costs nothing.

## Behavior measured on a free-plan key

- **Contact details are not included on the free plan.** `work_email`, `emails` and `mobile_phone` come back as `true`/`false` ("exists"), not as values. Names, job titles, levels, LinkedIn URLs and job-change dates are returned. Contacts are therefore written to the CRM without email; the provider only keeps an email when PDL returns an actual address. Add `--email-finder fullenrich` to find them (see [`email.md`](email.md)).
- **`job_title` is an exact field.** `match_phrase` "sales operations" only finds people titled exactly that (in a test, 0 people at a company with 653 people in sales). The provider uses wildcards (`*sales*operations*`); titles are stored lowercase.
- **Rate limit: 10 requests per minute.** Above that PDL answers 429 and spends nothing. The provider spaces calls by 6 seconds and retries a 429 once after the window, so a 25-account plan takes about 2.5 minutes.
- **The "remaining credits" header lags.** Per-call `x-call-credits-spent` was consistent; `x-totallimit-remaining` was sometimes off by a few credits between calls. Check the PDL dashboard for the authoritative balance.
- Company `employee_count` is PDL's estimate from the profiles it holds, so discovered headcounts are labeled `secondary`, never `verified`.

## Persona labels

Each returned person is assigned the persona whose phrase match ends furthest to the right in the title (the function usually comes last), then the longest phrase: "vice president of sales operations" is Revenue Operations, not sales leadership.

## Known limits

- Wildcards are broad: in a real run, "head of technical post-sales" matched `head of sales`. `--title-levels` narrows to seniority levels but PDL leaves `job_title_levels` empty on some records, which the filter then excludes.
- One Company Search call, up to 100 companies. No pagination yet.
- No signals: PDL discovery returns companies, not dated buying signals.
