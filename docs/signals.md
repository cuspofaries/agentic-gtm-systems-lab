# Buying signals (Exa)

`--signals exa` searches the web for dated events at each discovered company before qualification: a sales job opening, a new sales leader, funding or an expansion. The default is `none`.

```bash
export EXA_API_KEY=...
gtm-lab demo --discovery pdl --signals exa --example-dir path/to/your-context-dir
```

Signals feed the existing qualification rules. A fresh signal of a kind listed in `signal_policy.relevant_kinds` adds evidence, and with `policy.require_signal_for_qualification: true` an account without one is not qualified. Until this provider existed no real source produced signals, so that setting could only be used with the fixtures.

## What one account costs

One Exa search per account, type `lite`, limited to the last `signal_policy.max_signal_age_days` days, with page excerpts (`highlights`, no extra cost). Exa prices `lite` at $4 per 1,000 requests with up to 10 results; every response reports what it cost (`costDollars.total`, measured: `0.004`).

The budget is `signals.budget_millidollars` in `gtm-context.yaml`, in thousandths of a dollar because one search costs less than a cent. The default `100` ($0.10) covers 25 accounts. Before each search the workflow checks that its maximum cost still fits; it then charges what Exa reported. Accounts past the cap are not searched, and the run's events say so (`signals.budget_exceeded`). The review artifact shows the spend next to the enrichment spend.

## What is kept, what is rejected

Each result is checked by the code before it becomes a signal:

| Check | Rejected as | Why |
|---|---|---|
| The URL is a personal profile (`linkedin.com/in/...`) | `personal_profile` | A person, not a company event, and personal data. Its date is when the page was crawled. |
| The full company name appears only in the page body, not in the title, and the domain appears nowhere | `mentioned_in_passing` | Measured: a vendor's page with a table of several companies, one of them ours. The page is not about our company. |
| Neither the full company name nor its domain appears in the title, the excerpt or the URL host | `not_about_company` | A search for a company also returns pages about other companies and people. Names are matched as whole words: "Box" does not match "Dropbox". The first word of the domain is not used: on a real run it matched a different company with the same first word. |
| No publication date | `undated` | Freshness cannot be judged. |
| Published before the window | `outside_window` | Too old to count. |

The counts by reason are written to the run's events (`signals.account`), with the kept signals' kind, date and URL.

What is kept is labeled `secondary`, never `verified`: a page reports an event, it does not prove it. The date is the page's **publication date**, which can be later than the event.

## Kinds

Assigned by rules on the title and excerpt:

- `new_sales_leadership`: a sales or revenue leader title (CRO, VP Sales, VP Revenue, Head of Sales...) with an appointment verb (joins, names, hires, appointed...).
- `sales_hiring`: a sales role (account executive, account manager, SDR, sales engineer...) on a job page (job board host, `/jobs/` or `/careers/` path, or hiring words).
- `revenue_expansion`: funding, acquisition, expansion, new office.
- `other`: anything else, including section front pages (`/news`, `/press`, a LinkedIn company page), which list events without being one.

Only the kinds in `signal_policy.relevant_kinds` count as evidence; `other` is kept in the account's data for the reviewer.

## Measured on real companies

Two searches on 2026-11-22, $0.004 each:

- One search returned 5 results: a dated job posting for an Account Executive (a real `sales_hiring` signal), 3 personal LinkedIn profiles (one unrelated to the company) and the company's LinkedIn page. This is why the personal-profile and company checks exist.
- On the second company most results were LinkedIn posts by employees: a new VP of Revenue Operations, a restructuring, two hiring posts. The first rules read the VP post as `other` and the hiring posts as `revenue_expansion`; the rules were corrected and these cases are now unit tests.
- On the bundled fictional companies (3 searches), "BrightPath Analytics" first collected pages about a real, different BrightPath company, because the first word of its domain counted as the company. Only the full name and the domain count now. The same run kept a vendor's page that listed the company in a table among others; a name found only in the page body is now `mentioned_in_passing`.

## Limits

- A company whose name is a common word (one real example: "Front") can still match pages about something else. The reviewer sees each signal's title and URL.

- The kind is a rule on keywords. It misreads some pages; the reviewer sees the title and URL of every signal in the review artifact.
- One search per account finds what Exa ranks first, not every event.
- A step where Claude chooses its own searches, reads pages and checks quotes against them is planned; it is not implemented.

## Testing

`tests/unit/test_exa_signals.py` runs against a fake that reproduces the measured response. The end-to-end test on real data records and replays Exa answers like the other providers (`run.signals: exa` in `expected.yaml`, see [`real-case-test.md`](real-case-test.md)); a replay runs as of the recording date, so signal freshness gives the same result on any later day.
