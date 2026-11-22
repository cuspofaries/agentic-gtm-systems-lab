# Research loop

`--research exa` hands each account the rules cannot decide (`insufficient_evidence`) to Claude, with three tools. Claude chooses what to search, which pages to read and when to stop. The code bounds the loop and checks the evidence.

```bash
export EXA_API_KEY=... ANTHROPIC_API_KEY=...
gtm-lab demo --discovery pdl --research exa --research-steps 6 \
  --max-llm-cents 50 --research-exa-millidollars 50 --example-dir path/to/your-context-dir
```

It replaces `--agentic` for those accounts: `--agentic` asks Claude to decide from the account data alone; `--research` lets it gather more. The two cannot be combined. Accounts the rules decide never reach either.

## The tools

Shared with the opportunity investigator (`agents/research_tools.py`).

| Tool | What it does | Cost |
|---|---|---|
| `search(query, purpose)` | Exa `lite`, 5 results with URL, title, date and an excerpt. The code sets the window from `purpose`: `company_facts` searches `research_policy.company_facts_max_age_days` back (default 730), `recent_events` the signal window (`signal_policy.max_signal_age_days`). | $0.004 (Exa's reported cost is charged) |
| `read(url)` | The text of one page (Exa Contents, first 6,000 characters), given an id (`page-1`...). Only URLs returned by a search in this loop can be read. | $0.001 per page |
| `submit(assessment, explanation, claims)` | The facts found: per claim the field, the value as stated, the company the page is about, the URL, an exact quote. | none |

The tools are declared with `strict: true`. Tool use is not forced (Claude Opus 5.5 rejects forced tool use); an answer without a tool call ends the loop as `research_no_decision`.

## From claims to a decision

Claude does not decide the qualification. Its answer is a list of claims; the rest is code:

1. **Check each claim** (`SourcedClaim.check`): `quote_found` only if the page was read in this loop, the quote (8+ characters, whitespace and case aside) is in its text, and the page is about this company (on its domain, or naming its domain or full name). Otherwise `page_not_read`, `quote_not_found` or `subject_mismatch`.
2. **Reconcile per missing field** (`FactResolution`): all checked claims agree → `filled`; they disagree → `contradicted` (for headcount: some inside the ICP band, some outside, or a range that straddles it); none → `unresolved`. Only fields the provider left missing can be filled; provider data is never overwritten, and the stored account keeps the provider's values.
3. **Re-run the rules** on a copy of the account with the filled fields. That decision is final (`decided_by: "hybrid"`); Claude's own `assessment` is kept in the trace (`submitted`).
4. **Policy** reads the typed resolutions: a field filled by research must rest on a checked claim, and the "evidence references a missing field" check skips fields research filled.

If every claim of an answer fails its checks while two tool calls remain, the answer is sent back once as a tool error asking Claude to read the pages and resubmit (event outcome `sent_back`).

## What the code enforces

| Bound | Default | When reached |
|---|---|---|
| Tool calls per account (`--research-steps`) | 6, `submit` included | `insufficient_evidence`, missing `research_step_cap_reached` |
| LLM spend per run (`--max-llm-cents`) | 50¢ | Checked before every turn against its worst case (a high input estimate plus the full output cap); actual usage charged after. `research_llm_budget_would_exceed` |
| Exa spend per run (`--research-exa-millidollars`) | 50 ($0.05) | Checked before every search or read against its maximum. The tool answers that the budget is spent and Claude is asked to submit. |

API errors, refusals, a turn cut by the output cap and a missing answer all leave the account `insufficient_evidence`, with the reason in `missing`.

## Measured on the real API

These runs predate typed claims (Claude then submitted a decision with URL and quote evidence); the behavior they exposed is what the current design addresses.

Two runs on 2026-11-22 with Claude Opus 5.5 and Exa, on two real companies whose headcount was marked missing (ICP band 200 to 999 employees):

- **First run:** for both companies Claude ran one search and submitted quotes taken from the search excerpts, without reading the pages. The code dropped every item (no page read) and both accounts stayed `insufficient_evidence`. Cost: 4 LLM calls, 6¢; Exa $0.008.
- **Fix:** the prompt now says that excerpts are not evidence, and a submit whose evidence all fails is sent back once (as a tool error) while at least two tool calls remain, so Claude can read and resubmit.
- **Second run:** Claude searched once and read two pages per company before submitting, so the send-back was not needed (it is covered by a unit test). It submitted with quotes that the code found in the pages. One company had sources that disagree (189 and about 431 employees, one below the band and one inside it); Claude answered `insufficient_evidence` with both quotes. The other had 258 and about 435, both in the band: `qualified`. Cost: 6 LLM calls, 13¢; Exa $0.012.

## What it does not guarantee

The quote check proves that the words are on the page, not that the page is right. A page can be wrong, old, or about another company with the same name; a page can also contain text written to steer a model. Page text reaches Claude inside `<page>` tags with an instruction to treat it as data, the URL and quote are shown to the reviewer, and the plan still needs human approval before any CRM write.

## Audit

Every researched account gets a `qualification.fallback` event with the baseline decision, the research decision and `steps`: each tool call with its input, outcome and Exa cost, and the final `submit` with Claude's own assessment, how many claims passed and failed their checks, and the resolution of each field. `gtm-lab inspect <run-id>` shows them. The CLI prints the LLM calls and the LLM and Exa spend of the run.

## Testing

The end-to-end test on real data can record and replay the loop (`run.research: exa`, see [`real-case-test.md`](real-case-test.md)): Claude's answers and the Exa calls are stored, and a replay rebuilds the same requests offline.


`tests/unit/test_research_loop.py` scripts Claude's turns with a fake client and fakes Exa: the full search, read, submit path; quotes not in the page; evidence from a page never read; URLs not from a search; the step cap; both budgets; refusals, truncation, missing answers and API errors; and the events written by the workflow.
